"""WO-FCE-TOSS-US-STALL-01 — 영구 차단 래치 수리 회귀.

2026-07-28~29 사고 실측:
  · 07-28 13:51 UTC ~ 07-29 10:39 UTC (**20.8시간**) KR·US 양쪽 `market-calendar` 호출 0건
  · 그 사이 07-28 미국 정규장 잔여 6시간 + 07-29 한국 정규장 6.5시간 통째로 유실
  · 잡은 10초마다 "ok" 완주(runs 8,076 · 오류 0), 프로세스는 22.7시간 무중단
  · 결국 사람이 `/api/system/toss/auth-diagnosis` 를 때려서야 풀렸다

원인은 자기잠금 래치였다: 401 → 차단 등록 → 진입 즉시 반환(호출 안 함) → 성공 불가 → 차단 영구.

고정하는 명제:
1. 어떤 차단도 재시도 없이 굳지 않는다 — 백오프 경과 후 **반드시** 실호출한다 (C2)
2. 산출물 없는 반환은 전부 사유와 함께 관측된다 (C3)
3. 재시도는 백오프를 지킨다 — 무제한 재호출로 레이트 리밋을 위협하지 않는다 (C4)
4. 정지 알림은 "왜"를 함께 말한다 (작업 3)
5. US/KR 세션 판정 비대칭은 토스 응답 스펙이며, 뒤바꾸면 양쪽 다 오판한다 (작업 4)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import Settings
from app.toss import blocks, service
from app.toss.errors import TossAuthenticationError, TossEdgeBlocked
from app.worker import liveness


@pytest.fixture(autouse=True)
def _clean_registry():
    blocks.clear_all()
    blocks._outcomes.clear()
    service._latest_market.clear()
    yield
    blocks.clear_all()
    blocks._outcomes.clear()
    service._latest_market.clear()


def _settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'stall.db'}",
        toss_client_id="id",
        toss_client_secret="secret",
        toss_stock_scout_enabled=True,
    )


# ── 1. 자기잠금 금지 (C2 · 이 WO의 핵심) ────────────────────────────


def test_block_expires_and_allows_a_real_retry() -> None:
    """차단은 시각이 지나면 스스로 물러난다 — 이것이 없으면 20.8시간 사고가 재발한다."""
    block = blocks.register_block("US", "authentication_failed", "401")

    assert blocks.active_block("US") is block, "등록 직후엔 호출을 막아야 한다"
    assert blocks.pending_block("US") is None

    block.blocked_until = 0.0  # 백오프 경과
    assert blocks.active_block("US") is None, "만료된 차단이 호출을 계속 막으면 자기잠금이다"
    assert blocks.pending_block("US") is block, "재시도 대상으로 남아야 사전 조치(토큰 폐기)를 건다"


def test_backoff_grows_but_never_stops_retrying() -> None:
    """C4: 간격은 늘어나되 상한에서 멈춘다 — 재시도 자체가 사라지면 안 된다."""
    delays = [blocks.backoff_seconds("authentication_failed", n) for n in range(1, 8)]
    assert delays == [60, 120, 240, 480, 900, 900, 900]
    assert all(delay > 0 for delay in delays), "0이 되면 무제한 재호출(레이트 리밋 위반)"


def test_repeated_same_failure_escalates_and_new_reason_resets() -> None:
    first = blocks.register_block("US", "authentication_failed", "401")
    second = blocks.register_block("US", "authentication_failed", "401")
    assert (first.attempt_count, second.attempt_count) == (1, 2)
    assert second.first_blocked_at == first.first_blocked_at, "지속 시간은 최초 차단부터 센다"

    switched = blocks.register_block("US", "edge_blocked", "403")
    assert switched.attempt_count == 1, "다른 고장이면 다시 빠르게 확인해야 한다"


def test_every_block_status_has_a_retry_schedule() -> None:
    """auth 만 영구 래치였던 정책 불일치를 막는다 — 3종 모두 통일 구조."""
    for status in ("authentication_failed", "edge_blocked", "maintenance"):
        block = blocks.register_block("KR", status, "reason")
        assert block.blocked_until > 0
        assert blocks.backoff_seconds(status, 1) > 0
        blocks.clear_all()


@pytest.mark.asyncio
async def test_auth_failure_recovers_without_process_restart(tmp_path, monkeypatch) -> None:
    """수용 기준(핵심 증명): 401 주입 → 차단 → 백오프 경과 → **자동 재호출** → 복구.

    실제 사고에서는 재시작조차 통하지 않았다(22.7시간 무중단 상태로 20.8시간 정지).
    """
    settings = _settings(tmp_path)
    calls: list[str] = []
    failing = {"on": True}

    class _FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            self.invalidated = False

        def invalidate_cached_token(self) -> None:
            self.invalidated = True
            invalidations.append(True)

        async def get(self, path: str, **_kwargs):
            calls.append(path)
            if failing["on"]:
                raise TossAuthenticationError("401", status_code=401, request_id="r1")
            return {"today": {"regularMarket": None}}  # 호출 성공 · 휴장

        async def close(self) -> None:
            return None

    invalidations: list[bool] = []
    monkeypatch.setattr(service, "TossReadOnlyClient", _FakeClient)

    first = await service.collect_market(settings, "US")
    assert first["status"] == "authentication_failed"
    assert len(calls) == 1

    # 차단 중에는 호출하지 않는다(레이트 리밋 보호).
    blocked = await service.collect_market(settings, "US")
    assert blocked["status"] == "authentication_failed"
    assert len(calls) == 1, "차단 중 실호출이 나가면 백오프가 무의미하다"
    assert blocked["retry_in_seconds"] > 0

    # 백오프 경과 + 서버 정상화 → 재시작 없이 자동 복구.
    failing["on"] = False
    blocks._blocks["US"].blocked_until = 0.0
    recovered = await service.collect_market(settings, "US")

    assert len(calls) == 2, "백오프가 지났는데도 호출하지 않으면 자기잠금이다"
    assert invalidations, "인증 차단 재시도 전에 캐시 토큰을 버려야 즉시 재차단을 피한다"
    assert blocks.active_block("US") is None and blocks.pending_block("US") is None, "성공했으면 차단이 남으면 안 된다"
    assert recovered["status"] == "holiday"


@pytest.mark.asyncio
async def test_edge_blocked_stops_hammering_every_tick(tmp_path, monkeypatch) -> None:
    """D2: edge_blocked 는 래치가 없어 10초마다 무의미하게 재호출했다(C4 낭비)."""
    settings = _settings(tmp_path)
    calls: list[str] = []

    class _FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def invalidate_cached_token(self) -> None:
            return None

        async def get(self, path: str, **_kwargs):
            calls.append(path)
            raise TossEdgeBlocked("403", status_code=403, request_id="r2")

        async def close(self) -> None:
            return None

    monkeypatch.setattr(service, "TossReadOnlyClient", _FakeClient)
    for _ in range(30):  # 10초 주기 × 5분
        await service.collect_market(settings, "US")

    assert len(calls) == 1, f"30틱에 {len(calls)}회 호출 — 백오프가 안 걸렸다"
    assert blocks.blocks_snapshot()["US"]["status"] == "edge_blocked"


# ── 2. 조용한 조기 반환 금지 (C3 · D4) ──────────────────────────────


@pytest.mark.asyncio
async def test_early_return_is_recorded_with_a_reason(tmp_path, monkeypatch) -> None:
    """장 종료로 조기 반환해도 사유가 남아야 한다 — 예외가 없으면 흔적도 없다."""
    settings = _settings(tmp_path)

    class _FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def invalidate_cached_token(self) -> None:
            return None

        async def get(self, _path: str, **_kwargs):
            return {
                "today": {
                    "regularMarket": {
                        "startTime": "2026-07-29T22:30:00.000+09:00",
                        "endTime": "2026-07-30T05:00:00.000+09:00",
                    }
                }
            }

        async def close(self) -> None:
            return None

    monkeypatch.setattr(service, "TossReadOnlyClient", _FakeClient)
    response = await service.collect_market(settings, "US")

    assert response["status"] in {"closed", "open"}
    outcome = blocks.last_outcome("US")
    assert outcome is not None and outcome["status"] == response["status"]
    if response["status"] == "closed":
        assert "정규장" in (response["reason"] or ""), "판정 근거(파싱된 창)를 남겨야 오판을 식별한다"


def test_stall_reason_names_the_cause_and_duration() -> None:
    """작업 3: 'stock_us 정지'가 아니라 'authentication_failed N분 지속'이 보여야 한다."""
    block = blocks.register_block("US", "authentication_failed", "401")
    block.first_blocked_at = (datetime.now(timezone.utc) - timedelta(minutes=330)).isoformat()

    reason = blocks.stall_reasons()["US"]
    assert "authentication_failed" in reason
    assert "330분" in reason
    assert "재시도" in reason, "언제 스스로 풀리는지 없으면 사람이 또 코드를 뒤진다"


def test_liveness_alert_carries_the_reason() -> None:
    settings = Settings(database_url="memory://")
    us_session = datetime(2026, 7, 28, 15, 0, tzinfo=timezone.utc)
    market_data = {"stock_us": (us_session - timedelta(hours=6)).isoformat()}
    reasons = {"US": "authentication_failed 330분 지속 (재시도 60초 후 · 5회차)"}

    alerts = liveness.evaluate_liveness({}, settings, us_session, market_data, reasons)
    stale = [c for c in alerts if c.identity == "stock_us"]
    assert stale, "미국 장중 6시간 정지가 경보되지 않았다"
    assert "authentication_failed" in stale[0].message


# ── 3. 세션 판정 비대칭 (작업 4) ────────────────────────────────────

# 2026-07-29 실측 원본. KR 은 integrated 아래 중첩, US 는 평면 — 비대칭은 토스 스펙이다.
_KR_CALENDAR = {
    "today": {
        "date": "2026-07-29",
        "integrated": {"regularMarket": {"startTime": "2026-07-29T09:00:00.000+09:00", "endTime": "2026-07-29T15:30:00.000+09:00"}},
    }
}
_US_CALENDAR = {
    "today": {
        "date": "2026-07-29",
        "preMarket": {"startTime": "2026-07-29T17:00:00.000+09:00", "endTime": "2026-07-29T22:30:00.000+09:00"},
        "regularMarket": {"startTime": "2026-07-29T22:30:00.000+09:00", "endTime": "2026-07-30T05:00:00.000+09:00"},
    }
}


@pytest.mark.parametrize(
    "payload,market,moment,expected",
    [
        # US 정규장 = 22:30~05:00 KST = 13:30~20:00 UTC (EDT, 서머타임 적용)
        (_US_CALENDAR, "US", datetime(2026, 7, 29, 15, 0, tzinfo=timezone.utc), "open"),
        (_US_CALENDAR, "US", datetime(2026, 7, 29, 10, 43, tzinfo=timezone.utc), "closed"),  # 프리마켓
        (_US_CALENDAR, "US", datetime(2026, 7, 29, 20, 30, tzinfo=timezone.utc), "closed"),  # 애프터
        (_KR_CALENDAR, "KR", datetime(2026, 7, 29, 3, 0, tzinfo=timezone.utc), "open"),
        (_KR_CALENDAR, "KR", datetime(2026, 7, 29, 10, 43, tzinfo=timezone.utc), "closed"),
    ],
)
def test_session_state_uses_the_right_branch(payload, market, moment, expected) -> None:
    assert service._session_state(payload, market, now=moment) == expected


def test_swapping_the_branch_breaks_both_markets() -> None:
    """비대칭 분기를 '정리'하면 안 되는 이유 — 실측으로 고정한다(작업 4 유지 사유)."""
    moment = datetime(2026, 7, 29, 15, 0, tzinfo=timezone.utc)
    assert service._session_state(_US_CALENDAR, "KR", now=moment) == "holiday"
    assert service._session_state(_KR_CALENDAR, "US", now=moment) == "holiday"


# ── 4. 유실 구간을 정상 검증 기간으로 세지 않는다 (C5 · 작업 5) ─────


def test_verification_clock_excludes_lost_days() -> None:
    started = datetime(2026, 7, 21, tzinfo=timezone.utc)
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)
    # 실측: 07-23~27 3트랙 정지, 07-28 인증 래치 — 평가 흔적이 있는 날만 센다.
    effective = {"2026-07-21", "2026-07-22", "2026-07-28"}

    clock = liveness.elapsed_excluding_gaps(started, effective, now)

    assert clock["calendar_days"] == 8
    assert clock["effective_days"] == 3
    assert clock["lost_days"] == 5
    assert clock["label"] == "경과 3일 (유실 5일 제외)"


def test_concurrent_markets_block_independently() -> None:
    """KR·US 를 한 잡이 함께 수집하므로 한쪽 차단이 다른 쪽을 막으면 안 된다."""
    blocks.register_block("US", "authentication_failed", "401")
    assert blocks.active_block("US") is not None
    assert blocks.active_block("KR") is None


def test_registry_snapshot_is_json_safe() -> None:
    """진단 API 로 나가는 값 — 직렬화 실패로 관측 표면이 사라진 전례가 있다."""
    import json

    blocks.register_block("US", "authentication_failed", "401", {"request_id": "r1"})
    blocks.record_outcome("US", "authentication_failed", reason="401", block=blocks.active_block("US"))
    json.dumps({"blocks": blocks.blocks_snapshot(), "outcomes": blocks.outcomes_snapshot()})


def test_asyncio_loopless_token_invalidation_is_safe() -> None:
    """루프 밖 호출이 예외를 던지면 재시도 경로 자체가 죽는다."""
    from app.toss.client import TossReadOnlyClient

    client = TossReadOnlyClient("id", "secret")
    client.invalidate_cached_token()  # 실행 중 루프 없음 — 조용히 통과해야 한다
    asyncio.run(_noop())


async def _noop() -> None:
    return None


# ── 5. KST 자정 롤오버 (실측 2026-08-05) ────────────────────────────
#
# 토스 캘린더는 KST 날짜 기준이라 미국 정규장이 자정을 넘겨 이어진다. `today` 만 보면
# 00:00 KST(=15:00 UTC)에 아직 열려 있는 세션이 시야에서 사라져 closed 오판 → 수집 중단.
# 실측: US 는 매일 13:30 UTC 시작 → 14:57~14:59 UTC 정지(정규장 6.5h 중 1.5h만, 77% 유실).
#   07-29 13:30~14:40 · 07-31 13:30~14:59 · 08-03 13:30~14:59 · 08-04 13:30~14:57

# 자정 직후 실제 응답: today 가 08-06 으로 굴러가고, 열려 있는 세션은 previousBusinessDay 에 있다.
_US_AFTER_KST_MIDNIGHT = {
    "result": {
        "today": {
            "date": "2026-08-06",
            "regularMarket": {"startTime": "2026-08-06T22:30:00.000+09:00", "endTime": "2026-08-07T05:00:00.000+09:00"},
        },
        "previousBusinessDay": {
            "date": "2026-08-05",
            "regularMarket": {"startTime": "2026-08-05T22:30:00.000+09:00", "endTime": "2026-08-06T05:00:00.000+09:00"},
        },
        "nextBusinessDay": {
            "date": "2026-08-07",
            "regularMarket": {"startTime": "2026-08-07T22:30:00.000+09:00", "endTime": "2026-08-08T05:00:00.000+09:00"},
        },
    }
}


@pytest.mark.parametrize(
    "moment,expected,label",
    [
        (datetime(2026, 8, 5, 14, 59, tzinfo=timezone.utc), "open", "23:59 KST · 자정 직전"),
        (datetime(2026, 8, 5, 15, 1, tzinfo=timezone.utc), "open", "00:01 KST · 여기서 매일 끊겼다"),
        (datetime(2026, 8, 5, 17, 0, tzinfo=timezone.utc), "open", "02:00 KST · 13:00 ET 한창 장중"),
        (datetime(2026, 8, 5, 19, 59, tzinfo=timezone.utc), "open", "04:59 KST · 마감 1분 전"),
        (datetime(2026, 8, 5, 20, 1, tzinfo=timezone.utc), "closed", "05:01 KST · 실제 마감 후"),
        (datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc), "closed", "21:00 KST · 개장 전"),
    ],
)
def test_us_session_survives_kst_midnight_rollover(moment, expected, label) -> None:
    assert service._session_state(_US_AFTER_KST_MIDNIGHT, "US", now=moment) == expected, label


def test_rollover_regression_reproduces_the_old_bug() -> None:
    """today 만 봤다면 00:01 KST 에 closed 였다 — 이 대비가 수리의 증거다."""
    moment = datetime(2026, 8, 5, 15, 1, tzinfo=timezone.utc)
    today_only = {"result": {"today": _US_AFTER_KST_MIDNIGHT["result"]["today"]}}

    assert service._session_state(today_only, "US", now=moment) == "closed"  # 과거 동작
    assert service._session_state(_US_AFTER_KST_MIDNIGHT, "US", now=moment) == "open"  # 수리 후


def test_extra_day_candidates_never_invent_an_open_session() -> None:
    """후보를 늘려도 없는 개장을 만들면 안 된다 — 주말·휴장에 오탐이 나면 더 나쁘다."""
    weekend = datetime(2026, 8, 8, 15, 1, tzinfo=timezone.utc)  # 토요일
    assert service._session_state(_US_AFTER_KST_MIDNIGHT, "US", now=weekend) == "closed"


def test_kr_is_unaffected_by_the_rollover_fix() -> None:
    """KR 정규장은 자정을 넘지 않는다 — 후보 확장이 KR 판정을 흔들지 않아야 한다."""
    payload = {"result": {"today": _KR_CALENDAR["today"], "previousBusinessDay": {"date": "2026-07-28"}}}
    assert service._session_state(payload, "KR", now=datetime(2026, 7, 29, 3, 0, tzinfo=timezone.utc)) == "open"
    assert service._session_state(payload, "KR", now=datetime(2026, 7, 29, 10, 43, tzinfo=timezone.utc)) == "closed"
