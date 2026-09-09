"""열린 포지션 **위에서** "열린 포지션이 없습니다"가 찍힌 사건 (2026-09-09).

## 무엇이 보고됐나

일일 요약 상단이 이렇게 나갔다:

```
밤새 알림 요약
억제된 알림은 없습니다.

열린 포지션이 없습니다.          ← 이 시각 계좌에는 ZECUSDT 숏이 열려 있었다
```

## 왜

근거는 **이미 페이로드에 있었다.** 읽는 코드가 없었을 뿐이다:

| 근거 | 만드는 곳 | 읽던 곳 |
| --- | --- | --- |
| `sync_failed` | `WorkerManager._sync_positions` | 없음 |
| `sync_stale` · `sync_stale_note` | `WorkerManager._alert_payload` | 없음 |
| `positions_unavailable` | `sync_live_positions` | 정기 펄스만 |
| `open_count` | `sync_live_positions` | 없음 |
| 거래소 `status` | `_sync_bitget_positions` | 없음 |

게다가 동기화 주기가 한 번 실패하면 `_last_sync_payload` 가 **빈 페이로드로 교체**돼
마지막 성공 스냅샷까지 사라졌다. 그래서 실패 한 번이 곧 "포지션 없음"이었다.

이 파일이 고정하는 명제:

1. 근거 없이 "없음"을 단정하지 않는다 — 못 본 것과 없는 것은 다르다
2. 실패한 주기가 마지막 성공 스냅샷을 지우지 않는다
3. 단정할 수 없으면 **원장을 읽어** 가진 것을 말한다 (네트워크 없이)
4. 대조군 — 정상일 때 문구는 그대로다 (오탐 금지)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.notify.bot.formatters import format_positions_summary
from app.notify.lifecycle import pulse_candidate
from app.notify.position_visibility import (
    can_assert_empty,
    ledger_fallback_lines,
    observation_gap_lines,
)

NONE_CLAIM = "열린 포지션이 없습니다."


def _live_position(symbol: str = "ZECUSDT") -> dict:
    return {
        "position": {"symbol": symbol, "direction": "short", "leverage": 10},
        "state": {"pnl_percent": -111.95, "health_score": 12, "severity_rank": 4},
        "headline": "손실 확대 · 청산가 접근",
    }


# ── 명제 1: 근거 없이 "없음"을 단정하지 않는다 ──────────────────────────


def test_failed_sync_is_never_rendered_as_no_positions() -> None:
    """**이 한 줄이 사건 그 자체다.** 동기화가 죽은 것은 "포지션 없음"의 근거가 아니다."""
    text = format_positions_summary({"positions": [], "sync_failed": True})

    assert NONE_CLAIM not in text
    assert "판정 불가" in text
    assert "동기화 실패" in text


def test_stale_sync_is_never_rendered_as_no_positions() -> None:
    text = format_positions_summary({"positions": [], "sync_stale": True, "sync_stale_note": "포지션 동기화가 42분째 갱신되지 않았다"})

    assert NONE_CLAIM not in text
    assert "42분째" in text


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("error", "거래소 API 오류"),
        ("permission_error", "거래소 API 권한 오류"),
        ("not_configured", "거래소 API 키 미설정"),
        ("not_active", "거래소 어댑터 비활성"),
    ],
)
def test_exchange_status_is_named_not_swallowed(status: str, expected: str) -> None:
    """거래소를 못 읽은 것과 포지션이 없는 것은 다르다. 사유가 이름을 가져야 조치가 된다."""
    text = format_positions_summary({"positions": [], "status": status, "error": "40037 apikey does not exist"})

    assert NONE_CLAIM not in text
    assert expected in text
    assert "40037" in text


def test_unavailable_positions_are_listed_with_reasons() -> None:
    """원장엔 있는데 분석이 실패해 빠진 포지션. 조용히 사라지면 안 된다."""
    text = format_positions_summary(
        {
            "positions": [],
            "open_count": 1,
            "positions_unavailable": [{"symbol": "ZECUSDT", "reason": "422: candles unavailable"}],
        }
    )

    assert NONE_CLAIM not in text
    assert "ZECUSDT" in text
    assert "422: candles unavailable" in text
    assert "포지션은 열려 있다" in text


def test_unexplained_gap_between_ledger_and_render_is_reported() -> None:
    """사유조차 없는 공백이 가장 위험하다 — 그것을 따로 센다."""
    text = format_positions_summary({"positions": [], "open_count": 2})

    assert NONE_CLAIM not in text
    assert "2건 중 0건" in text


def test_partial_render_keeps_both_the_list_and_the_gap() -> None:
    """일부만 보였을 때 목록만 보내면 나머지는 없는 것이 된다."""
    text = format_positions_summary(
        {
            "positions": [_live_position()],
            "open_count": 2,
            "positions_unavailable": [{"symbol": "BTCUSDT", "reason": "502: upstream"}],
        }
    )

    assert "ZECUSDT" in text
    assert "BTCUSDT" in text and "502: upstream" in text


def test_can_assert_empty_is_the_single_predicate() -> None:
    """ "없음"을 말해도 되는지 묻는 곳은 하나다 — 경로마다 다시 판단하면 또 갈린다."""
    assert can_assert_empty({"positions": [], "open_count": 0, "status": "ok"}) is True
    assert can_assert_empty({"positions": [], "sync_failed": True}) is False
    assert can_assert_empty({"positions": [], "open_count": 1}, rendered=0) is False


# ── 명제 2: 실패한 주기가 마지막 성공 스냅샷을 지우지 않는다 ────────────


class _Manager:
    """`_alert_payload` 계약만 흉내낸다 — 워커 전체를 세우지 않는다."""

    def __init__(self, settings, payload, last_sync_at, last_sync_failed) -> None:
        self.settings = settings
        self._last_sync_payload = payload
        self._last_sync_at = last_sync_at
        self._last_sync_failed = last_sync_failed


def test_failed_cycle_keeps_the_last_good_snapshot() -> None:
    """실패 한 번이 포지션 목록을 지우면, 그 다음 알림은 전부 "없음" 위에서 찍힌다."""
    from app.core.config import Settings
    from app.worker.manager import WorkerManager

    manager = _Manager(
        Settings(),
        {"positions": [_live_position()], "open_count": 1},
        datetime.now(timezone.utc) - timedelta(minutes=2),
        True,
    )
    payload = WorkerManager._alert_payload(manager)

    assert payload["positions"], "실패한 주기가 마지막 성공 스냅샷을 지웠다"
    assert payload["sync_failed"] is True, "실패 사실이 실려야 한다 — 낡은 값을 신선한 척 보내면 안 된다"
    assert NONE_CLAIM not in format_positions_summary(payload)


def test_sync_job_only_replaces_the_snapshot_on_success() -> None:
    """구조 고정 — 실패 경로에서 `_last_sync_payload` 를 덮어쓰면 위 계약이 깨진다."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app/worker/manager.py").read_text(encoding="utf-8")
    block = source.split("async def _sync_positions")[1].split("\n    def _alert_payload")[0]
    failure_branch = block.split("if not isinstance(payload, dict):")[1].split("else:")[0]

    assert "_last_sync_payload" not in failure_branch, "실패 주기가 마지막 성공 스냅샷을 덮어쓴다"
    assert "_queue_lifecycle" not in failure_branch, "실패 페이로드로 라이프사이클을 다시 큐에 넣는다"


# ── 명제 3: 단정할 수 없으면 원장을 읽는다 ──────────────────────────────


def test_ledger_fallback_says_what_we_have() -> None:
    rows = [{"symbol": "ZECUSDT", "direction": "short", "leverage": 10, "entry_price": 1047.44}]

    lines = ledger_fallback_lines(rows)

    assert "ZECUSDT" in "\n".join(lines)
    assert "숏" in "\n".join(lines)
    assert "1047.44" in "\n".join(lines)


def test_ledger_fallback_does_not_claim_certainty_when_empty() -> None:
    """원장도 비었다고 "없다"로 끝내지 않는다 — 거래소는 여전히 못 읽었다."""
    text = "\n".join(ledger_fallback_lines([]))

    assert "확인되지 않았다" in text


@pytest.mark.asyncio
async def test_daily_summary_block_falls_back_to_the_ledger(tmp_path) -> None:
    """동기화가 죽은 밤에도 요약은 **가진 것을 말한다.**"""
    from uuid import uuid4

    from app.api.deps import configure_runtime, get_repository
    from app.core.config import Settings
    from app.db.models import Direction, Position, PositionStatus
    from app.db.repository import MemoryRepository
    from app.exchange.mock import MockMarketDataProvider
    from app.notify.alerts import AlertEngine
    from app.notify.state import NotificationState

    configure_runtime(repo=MemoryRepository(), provider=MockMarketDataProvider())
    get_repository().add_position(
        Position(
            id=uuid4(),
            symbol="ZECUSDT",
            direction=Direction.short,
            entry_price=1047.44,
            quantity=0.657,
            leverage=10,
            status=PositionStatus.open,
            source="bitget",
        )
    )

    class _Sender:
        enabled = True

        async def send_to_all(self, text: str, *, reply_markup=None) -> int:
            return 1

    engine = AlertEngine(
        Settings(notification_state_path=str(tmp_path / "state.json")),
        _Sender(),
        NotificationState(),
    )
    text = await engine._positions_block({"positions": [], "sync_failed": True})

    assert NONE_CLAIM not in text
    assert "ZECUSDT" in text, "원장에 열린 포지션이 있는데 요약이 그것을 말하지 않았다"


def test_ledger_fallback_does_not_touch_the_network() -> None:
    """대체 경로가 동기화를 죽인 그 원인으로 같이 죽으면 대체가 아니다."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app/services/runtime.py").read_text(encoding="utf-8")
    block = source.split("def open_positions_ledger")[1].split("\ndef ")[0]

    for network in ("_generate_and_store_report", "_live_position_payload", "get_snapshot", "get_positions"):
        assert network not in block, f"대체 경로가 네트워크를 탄다: {network}"


def test_one_bad_symbol_does_not_erase_the_whole_listing(monkeypatch) -> None:
    """`/positions` 가 통째로 실패하면 그 침묵은 "포지션 없음"과 구분되지 않는다.

    이전에는 리스트 컴프리헨션 안에서 `HTTPException` 이 그대로 올라와 응답 자체가 사라졌다.
    """
    from uuid import uuid4

    from fastapi import HTTPException

    from app.api.deps import configure_runtime, get_repository
    from app.db.models import Direction, Position, PositionStatus
    from app.db.repository import MemoryRepository
    from app.exchange.mock import MockMarketDataProvider
    from app.services import http_handlers

    configure_runtime(repo=MemoryRepository(), provider=MockMarketDataProvider())
    for symbol in ("ZECUSDT", "BTCUSDT"):
        get_repository().add_position(
            Position(
                id=uuid4(),
                symbol=symbol,
                direction=Direction.short,
                entry_price=100.0,
                quantity=1.0,
                status=PositionStatus.open,
                source="bitget",
            )
        )

    def _payload(position, store_snapshot=False):
        if position.symbol == "ZECUSDT":
            raise HTTPException(status_code=422, detail="candles unavailable")
        return {"position": position.model_dump(mode="json"), "state": {}}

    monkeypatch.setattr(http_handlers, "_live_position_payload", _payload)
    result = http_handlers.list_live_positions()

    assert result["open_count"] == 2
    assert len(result["positions"]) == 1, "성한 포지션까지 같이 사라졌다"
    assert result["positions_unavailable"][0]["symbol"] == "ZECUSDT"
    assert NONE_CLAIM not in format_positions_summary(result)
    assert "candles unavailable" in format_positions_summary(result)


# ── 펄스도 같은 위장을 하지 않는다 ──────────────────────────────────────


def test_pulse_does_not_report_normal_over_a_dead_sync() -> None:
    """`unavailable` 만 고쳐 뒀던 절반의 수리를 마저 한다."""
    candidate = pulse_candidate([], gap_lines=["⚠ 포지션 동기화 실패 — 직전 주기가 실패했다"])

    assert candidate is not None
    assert "감시 정상 동작 중입니다" not in candidate.message
    assert "동기화 실패" in candidate.message


def test_pulse_does_not_say_all_normal_over_a_gap() -> None:
    """본 것만 정상이지 "전부"가 아니다."""
    candidate = pulse_candidate(
        [_live_position()],
        unavailable=[{"symbol": "BTCUSDT", "reason": "502: upstream"}],
    )

    assert candidate is not None
    assert "전부 정상" not in candidate.message


# ── 명제 4: 대조군 — 정상일 때는 문구가 그대로다 ────────────────────────


def test_healthy_empty_payload_still_says_no_positions() -> None:
    """오탐 금지. 이 문구가 사라지면 이번엔 반대 방향으로 거짓말을 하는 것이다."""
    text = format_positions_summary({"positions": [], "open_count": 0, "status": "ok", "sync_stale": False, "synced": 0, "product_type": "USDT-FUTURES"})

    assert text.startswith(NONE_CLAIM)
    assert "판정 불가" not in text


# ── 2차 보고: 사유가 없어도 **출처**는 댄다 ─────────────────────────────


def test_no_positions_claim_carries_its_source() -> None:
    """거래소 앱엔 포지션이 보이는데 여기가 0건이면, 문제는 표시가 아니라 거래소 조회다.

    사용자 2차 보고(2026-09-09 13:16 펄스): "여전히 없다고 나오는데 뭔소리야."
    동기화가 성공하고 원장도 0건이면 1차 수리의 사유 줄은 하나도 뜨지 않는다 — 우리 판정은
    옳지만 화면과 계좌가 어긋난 사실은 여전히 감춰진다. 출처를 적어 그 어긋남을 국소화한다.
    """
    text = format_positions_summary({"positions": [], "open_count": 0, "status": "ok", "synced": 0, "product_type": "USDT-FUTURES", "sync_age_seconds": 240})

    assert text.startswith(NONE_CLAIM)
    assert "거래소 USDT-FUTURES 0건" in text, "거래소가 몇 건을 줬는지가 없으면 해석이 안 된다"
    assert "원장 0건" in text
    assert "동기화 4분 전" in text


def test_pulse_empty_note_localises_the_mismatch() -> None:
    """ "감시 정상"만으로는 사용자가 어디를 볼지 알 수 없다."""
    candidate = pulse_candidate([], empty_note="거래소 USDT-FUTURES 0건 · 원장 0건")

    assert candidate is not None
    assert "거래소 USDT-FUTURES 0건" in candidate.message
    assert "감시 정상 동작 중입니다" in candidate.message, "정상 판정 자체는 유지된다"


def test_sync_result_reports_the_product_type_it_queried() -> None:
    """0건의 해석은 productType 없이는 불가능하다 — 계정 유형 변경이 여기서 0건을 만든다."""
    from pathlib import Path as _Path

    source = (_Path(__file__).resolve().parents[1] / "app/services/http_handlers.py").read_text(encoding="utf-8")
    block = source.split("def _sync_bitget_positions")[1].split("\ndef ")[0]

    assert '"product_type"' in block


def test_healthy_list_has_no_gap_footer() -> None:
    payload = {"positions": [_live_position()], "open_count": 1, "status": "ok"}

    assert observation_gap_lines(payload, rendered=1) == []
    assert "판정 불가" not in format_positions_summary(payload)


def test_pulse_still_says_normal_when_nothing_is_wrong() -> None:
    candidate = pulse_candidate([])

    assert candidate is not None
    assert "감시 정상 동작 중입니다" in candidate.message
