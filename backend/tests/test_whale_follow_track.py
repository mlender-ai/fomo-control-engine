"""WO-FCE-WHALE-FOLLOW-02 — 추종 자격 · 이벤트 구동 진입 · 알림 계약.

**규칙 한 줄**: 승률 좋은 고래가 들어간 가격 근처에서 같이 들어간다.

Phase 6(-01)은 배선은 됐지만 세 가지가 틀렸다 — 4시간 늦게 들어갔고, 아무나 따라갔고,
자격 체계가 쓸데없이 복잡했다. 이 파일이 고정하는 것은 그 셋의 수리다.
승격 기준은 여전히 한 글자도 바뀌지 않는다(C3).
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.db.models import Direction, MarketCandle, PaperTrade
from app.notify import delivery_gate, whale_follow_alerts
from app.onchain import follow_eligibility as fe
from app.onchain import participant_type
from app.paper import policy as paper_policy
from app.paper import whale_follow

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)

UNTOUCHABLE = ("backend/app/paper/policy.py", "backend/app/analyst", "backend/app/structure")


# ── 7-1 추종 자격: 규칙 하나, 조건 셋 ─────────────────────────────────


def _status(**overrides):
    base = {
        "address": "0xabc",
        "sample_size": 37,
        "wins": 24,  # 64.9%
        "ci_low": 40.0,
        "estimate": {"participant_type": participant_type.TYPE_DIRECTIONAL},
    }
    return fe.follow_status(**{**base, **overrides})


def test_the_rule_is_exactly_three_conditions() -> None:
    """N>=30 · 승률 점추정 >=55% · MM 아님. 그 이상도 이하도 아니다(§2)."""
    assert fe.FOLLOW_MIN_SAMPLE == 30
    assert fe.FOLLOW_MIN_WIN_PCT == 55.0
    assert fe.FOLLOW_EXCLUDED_TYPES == frozenset({participant_type.TYPE_MARKET_MAKER})


def test_sample_below_thirty_is_rejected() -> None:
    status = _status(sample_size=29, wins=29)
    assert status.eligible is False
    assert "29/30" in status.reason


def test_win_rate_below_the_bar_is_rejected() -> None:
    """54.9% 는 통과하지 않는다. 동전 던지기를 신호로 삼지 않는다."""
    status = _status(sample_size=100, wins=54)
    assert status.eligible is False
    assert "54.0%" in status.reason


def test_win_rate_exactly_at_the_bar_passes() -> None:
    """55.0% 는 '이상'이다 — 경계를 어느 쪽으로 정했는지 코드가 말하게 한다."""
    status = _status(sample_size=100, wins=55)
    assert status.eligible is True


def test_market_maker_is_never_eligible() -> None:
    """MM 체결은 방향 베팅이 아니라 재고 관리다 — 정의상 잡음이다."""
    status = _status(estimate={"participant_type": participant_type.TYPE_MARKET_MAKER}, sample_size=999, wins=999)
    assert status.eligible is False


def test_unclassified_is_allowed() -> None:
    """모르는 것을 배제하면 영원히 모른다. 승률 1위 지갑이 `unclassified` 다."""
    status = _status(estimate={"participant_type": participant_type.TYPE_UNCLASSIFIED})
    assert status.eligible is True
    assert status.unclassified_flag is True


def test_confidence_zero_wallet_is_filtered_by_the_win_rate_bar() -> None:
    """**7-1 수용 기준** — 실측 `0x1ee7…edf5` (신뢰 0.0 · N=39 · 점추정 ~51%) 가 탈락한다.

    Phase 6 은 이 지갑으로 거래하고 있었다. 신뢰도를 자격에서 뺐지만 승률 조건이 잡는다 —
    조건을 늘리지 않고도 걸러진다는 것이 이 규칙이 단순해도 되는 이유다.
    """
    status = _status(
        address="0x1ee7edf5",
        sample_size=39,
        wins=20,  # 51.3%
        ci_low=35.9,
        estimate={"participant_type": participant_type.TYPE_UNCLASSIFIED, "confidence": 0.0},
    )
    assert status.eligible is False, "신뢰 0.0 · CI 하한 35.9% 지갑이 여전히 통과한다"
    assert "51.3%" in status.reason


def test_ci_low_and_confidence_are_display_only() -> None:
    """§2 — 자격 판정에서 뺐다. 표시에는 남긴다."""
    high_ci = _status(ci_low=80.0)
    low_ci = _status(ci_low=1.0)
    assert high_ci.eligible == low_ci.eligible, "CI 하한이 판정을 움직였다"

    payload = _status(estimate={"participant_type": participant_type.TYPE_DIRECTIONAL, "confidence": 0.87}).as_payload()
    assert payload["participant_confidence"] == 0.87
    assert set(payload["display_only"]) == {"ci_low", "participant_confidence"}


def test_qualification_is_a_single_axis() -> None:
    """7-1 항목 2 — 관찰/승격 2축을 걷어냈다. 추종 자격은 하나다."""
    assert fe.QUALIFICATION_FOLLOW == "follow"
    for removed in ("QUALIFICATION_OBSERVATION", "QUALIFICATION_PROMOTION", "qualification_for"):
        assert not hasattr(fe, removed), f"2축 자격이 남아 있다: {removed}"


def test_every_status_carries_the_unverified_label() -> None:
    """C8 — 추종 자격이 승격으로 읽히면 안 된다."""
    payload = _status().as_payload()
    assert payload["label"] == "미검증 추종 자격"
    assert "승격 근거로 쓰지 않는다" in payload["not_promotion"]


def test_contaminated_sample_is_named_in_the_reason() -> None:
    """계수에서 뺐다는 사실이 사유에 남는다(C10)."""
    status = _status(sample_size=0, wins=0, excluded_sample=11)
    assert status.eligible is False
    assert "오염 11건 제외" in status.reason


def test_summary_names_the_passers_and_refuses_to_lower_the_bar() -> None:
    """7-1 항목 3·4 — 통과자 목록을 내고, 0명이면 그 사실을 명시한다."""
    passing = fe.follow_status(address="0xgood", sample_size=37, wins=24, ci_low=44.0, estimate={"participant_type": participant_type.TYPE_DIRECTIONAL})
    failing = fe.follow_status(address="0xbad", sample_size=39, wins=20, ci_low=35.9, estimate={"participant_type": participant_type.TYPE_UNCLASSIFIED})

    summary = fe.summary({"0xgood": passing, "0xbad": failing})
    assert summary["eligible"] == 1
    assert summary["passers"][0]["address"] == "0xgood"
    assert summary["passers"][0]["win_pct"] == 64.9
    assert any(row["address"] == "0xbad" for row in summary["rejected"]), "탈락 사유가 사라졌다"
    assert summary["zero_passers_note"] is None

    empty = fe.summary({"0xbad": failing})
    assert empty["eligible"] == 0
    assert "기준을 낮추지 않는다" in empty["zero_passers_note"]


def test_eligibility_module_does_not_read_promotion_thresholds() -> None:
    """C3 — 두 축이 서로를 참조하지 않는다."""
    source = (REPO_ROOT / "backend/app/onchain/follow_eligibility.py").read_text(encoding="utf-8")
    for forbidden in ("WHALE_VALIDATION_DAYS", "trust_status", "_wallet_review"):
        assert forbidden not in source.replace("28일 · N>=30 · CI 하한 55%", ""), f"승격 판정을 참조한다: {forbidden}"


# ── 7-2 이벤트 구동 진입 ★ ────────────────────────────────────────────


class _Event:
    def __init__(self, address: str, symbol: str, side: str, event: str, at: datetime, size_usd: float = 500_000.0, entry_px: float = 100.0) -> None:
        self.wallet_address = address
        self.symbol = symbol
        self.side = side
        self.event = event
        self.event_at = at
        self.size_usd = size_usd
        self.entry_px = entry_px
        self.wallet_label = "고래"


ELIGIBLE = {"0xa": fe.QUALIFICATION_FOLLOW}


def _signals(events, *, now=NOW, horizon=120):
    return whale_follow.entry_signals(events, eligible=ELIGIBLE, now=now, scan_horizon_minutes=horizon)


def test_only_increase_events_trigger_entries() -> None:
    """감액·청산은 진입 신호가 아니다 — 별도 판단이다."""
    events = [
        _Event("0xa", "BTCUSDT", "long", "increase", NOW),
        _Event("0xa", "ETHUSDT", "long", "reduce", NOW),
        _Event("0xa", "SOLUSDT", "short", "close", NOW),
        _Event("0xa", "XRPUSDT", "short", "flip", NOW),
    ]
    assert {signal["symbol"] for signal in _signals(events)} == {"BTCUSDT", "XRPUSDT"}


def test_ineligible_wallets_produce_no_signal() -> None:
    assert _signals([_Event("0xbad", "BTCUSDT", "long", "increase", NOW)]) == []


def test_latest_event_per_wallet_symbol_wins() -> None:
    events = [
        _Event("0xa", "BTCUSDT", "long", "increase", NOW - timedelta(minutes=30)),
        _Event("0xa", "BTCUSDT", "short", "flip", NOW - timedelta(minutes=5)),
    ]
    signals = _signals(events)
    assert len(signals) == 1
    assert signals[0]["direction"] == Direction.short


def test_signals_carry_the_whale_fill_price() -> None:
    """이탈 상한의 기준선이다. 없으면 상한을 걸 수 없다(7-2 항목 4)."""
    signals = _signals([_Event("0xa", "BTCUSDT", "long", "increase", NOW, entry_px=2478.1)])
    assert signals[0]["whale_price"] == 2478.1


def test_freshest_signal_is_evaluated_first() -> None:
    """지연이 이 트랙의 성패다 — 큰 금액보다 덜 늙은 것을 먼저 본다."""
    events = [
        _Event("0xa", "BTCUSDT", "long", "increase", NOW - timedelta(minutes=25), size_usd=9_000_000.0),
        _Event("0xa", "ETHUSDT", "long", "increase", NOW - timedelta(minutes=1), size_usd=1_000.0),
    ]
    assert [signal["symbol"] for signal in _signals(events)] == ["ETHUSDT", "BTCUSDT"]


# ── 진입가는 현재가다 (7-2 항목 2) ─────────────────────────────────────


CONFIRMED = MarketCandle(timestamp=NOW - timedelta(hours=3), open=100.0, high=101.0, low=99.0, close=100.0, volume=10.0)


def _candidate(**overrides):
    policy = paper_policy.PaperPolicy(margin_usdt=100.0, leverage=5.0)
    base = {
        "signal": {
            "address": "0xa",
            "qualification": fe.QUALIFICATION_FOLLOW,
            "symbol": "BTCUSDT",
            "direction": Direction.long,
            "event_at": NOW - timedelta(minutes=12),
            "event": "increase",
            "size_usd": 900_000.0,
            "wallet_label": "고래",
            "whale_price": 103.0,
            "win_pct": 64.9,
            "participant_type": "unclassified",
            "unclassified_flag": True,
            "sample_size": 37,
            "ci_low": 44.0,
        },
        # 확정봉은 3시간 묵었다. **진입가로 쓰이면 안 된다.**
        "bar": CONFIRMED,
        "entry_price": 103.5,
        "price_at": NOW - timedelta(minutes=1),
        "drift": whale_follow.price_drift(whale_price=103.0, entry_price=103.5, direction=Direction.long, stop_distance=6.5),
        "asset_class": "crypto",
        "policy": policy,
        "invalidation": 97.0,
        "take_profit": 112.0,
        "take_profit_2": 120.0,
        "entry_atr": 2.0,
        "target_plan": {},
        "simulation": {},
        "timeframe": "4h",
    }
    return {**base, **overrides}


def test_entry_price_is_the_live_price_not_the_confirmed_close() -> None:
    """**7-2 핵심 수용 기준.** 3시간 묵은 확정봉 종가(100.0)로 들어가면 추종이 아니다."""
    trade = whale_follow.open_follow_trade(_candidate(), now=NOW)
    assert trade.entry_price == pytest.approx(103.5), "확정봉 종가로 진입했다 — Phase 6 의 결함이 그대로다"
    assert trade.entry_price != CONFIRMED.close
    assert (trade.entry_evidence or {})["entry_price_source"] == "provider_mark_price"


def test_end_to_end_entry_price_differs_from_the_confirmed_close() -> None:
    """이 결함이 숨었던 지점. 픽스처가 캔들 종가와 현재가를 **같게** 만들면 통과한다.

    실제 payload 는 둘이 다르다 — 확정봉은 최대 4시간 전이고 마크가는 지금이다.
    `_candidate()` 는 진입가를 미리 넣으므로 `live_price` 를 타지 않는다. 그래서 전 구간을
    돌려 진입가가 **마크가**로 잡히는지 본다.
    """
    payload = _analysis(103.5, confirmed_close=100.0)
    repo = _Repo([_Event("0xa", "BTCUSDT", "long", "increase", NOW - timedelta(minutes=5), entry_px=103.4)])
    result = whale_follow.run_entries(
        repo,
        Settings(),
        eligible=ELIGIBLE,
        analysis_loader=lambda symbol, timeframe: payload,
        simulation_loader=lambda *args: {"action_plan": {"invalidation": 97.0}, "survives_to_invalidation": True},
        now=NOW,
    )
    assert result["opened"] == 1, f"진입하지 못했다: {result['rejected']}"
    entered = repo.saved[-1]
    assert entered.entry_price == pytest.approx(103.5), "확정봉 종가(100.0)로 진입했다"
    assert entered.entry_price != 100.0


def test_entry_time_is_the_wall_clock_not_the_bar() -> None:
    """봉 마감을 기다리지 않는다 — 진입 시각은 판단한 순간이다."""
    trade = whale_follow.open_follow_trade(_candidate(), now=NOW)
    assert trade.entry_at == NOW
    # 재진입 잠금·출구 판정의 기준 봉은 확정봉 그대로다(C4).
    assert trade.entry_bar_at == CONFIRMED.timestamp


def test_sizing_uses_the_live_price(monkeypatch) -> None:
    """C4 — 사이징 식은 그대로이고 **입력만** 현재가로 바뀐다."""
    trade = whale_follow.open_follow_trade(_candidate(), now=NOW)
    expected = paper_policy.plan_position_size(entry_price=103.5, invalidation_price=97.0, policy=paper_policy.PaperPolicy(margin_usdt=100.0, leverage=5.0))
    assert trade.quantity == pytest.approx(expected["quantity"])
    assert (trade.target_plan or {})["sizing"]["mode"] == expected["mode"]


def test_live_price_reads_the_provider_mark() -> None:
    """현재가는 마크가다. 캔들 마지막 봉이 아니다.

    이 저장소의 `candles` 는 **확정봉만** 담는다(`MTF-PATTERN-01` 이 미확정 진행 봉을
    분석 입력에서 제거했다). 그래서 `candles[-1].close` 는 `_confirmed_bar` 와 같은 값이고,
    그것을 진입가로 쓰면 4시간(4h봉) 묵은 가격에 들어간다 — 7-2 가 고치려던 결함 그대로다.

    2026-08-25T14:06Z 실측 BTCUSDT: 캔들 경로 79,090.8(6시간) vs 마크가 78,817.4(2초).
    """
    analysis = {
        "mark_price": 103.5,
        # 캔들에는 확정봉만 있고, 그 종가는 마크가와 다르다. 캔들을 읽으면 이 값이 나온다.
        "candles": [{"time": (NOW - timedelta(hours=4)).isoformat(), "close": 100.0}],
    }
    price, stamp = whale_follow.live_price(analysis, as_of=NOW.isoformat())
    assert price == 103.5, "마크가가 아니라 확정봉 종가를 읽었다 — 7-2 의 결함이 되살아났다"
    assert stamp == NOW


def test_live_price_never_falls_back_to_candles() -> None:
    """폴백하면 결함이 조용히 되살아난다. 없으면 없다고 낸다."""
    assert whale_follow.live_price({"candles": [{"time": NOW.isoformat(), "close": 12345.0}]}) == (None, None)
    assert whale_follow.live_price({}) == (None, None)
    assert whale_follow.live_price({"mark_price": 0.0, "candles": [{"close": 999.0}]}) == (None, None)


def test_live_price_accepts_the_documented_aliases() -> None:
    """제공자에 따라 필드명이 다르다. 실측 payload 는 `mark_price` 와 `price_levels.mark` 를 함께 준다."""
    assert whale_follow.live_price({"price_levels": {"mark": 77.0}})[0] == 77.0
    assert whale_follow.live_price({"liquidity": {"reference_price": 88.0}})[0] == 88.0


# ── 지연 상한 · 이탈 상한이 거부로 동작한다 (7-2 항목 3·4) ─────────────


class _Repo:
    def __init__(self, events) -> None:
        self._events = events
        self.saved: list = []

    def list_whale_events(self, wallet_address=None, limit=200):
        return [event for event in self._events if event.wallet_address == wallet_address]

    def list_whale_follow_trades(self, status=None, symbol=None, limit=500):
        return []

    def upsert_whale_follow_trade(self, trade):
        self.saved.append(trade)

    def list_paper_trades(self, status=None, symbol=None, limit=500):
        """재진입 잠금이 **크립토 원장**을 읽는다 — 원장을 갈라 놓고 잠금만 공유한다.

        Phase 6 부터의 동작이고 C4 가 잠금 수정을 금지해 손대지 않았다. 이 스텁은 그 결합이
        존재한다는 사실을 픽스처에 남긴다 — 없으면 `AttributeError` 로만 드러난다.
        """
        return []


def _analysis(price: float, *, confirmed_close: float | None = None) -> dict:
    """분석 페이로드. **현재가는 `mark_price`** 이고 캔들은 확정봉이다.

    실제 payload 가 그 형태다 — `candles` 에는 확정봉만 들어오고 현재가는 마크가로 온다.
    `confirmed_close` 를 따로 주면 "확정봉 종가 ≠ 현재가"인 실전 상황을 만든다.
    """
    close = price if confirmed_close is None else confirmed_close
    return {
        "as_of": NOW.isoformat(),
        "analysis": {
            "asset_class": "crypto",
            "mark_price": price,
            "candles": [{"time": NOW.isoformat(), "open": close, "high": close, "low": close, "close": close, "volume": 1.0}],
        },
    }


def _run(events, *, loader=None, **kwargs):
    return whale_follow.run_entries(
        _Repo(events),
        object(),
        eligible=ELIGIBLE,
        analysis_loader=loader or (lambda symbol, timeframe: {}),
        simulation_loader=lambda *args: {},
        now=NOW,
        **kwargs,
    )


def test_latency_cap_rejects_it_does_not_merely_record() -> None:
    """**Phase 6 은 기록만 하고 거부하지 않았다.** 상한 없는 관측치는 변명이다."""
    result = _run([_Event("0xa", "BTCUSDT", "long", "increase", NOW - timedelta(minutes=45))], max_latency_minutes=30)
    assert result["opened"] == 0
    assert result["rejection_summary"]["by_reason"] == {whale_follow.REASON_LATENCY: 1}


def test_latency_cap_is_applied_before_any_analysis_lookup() -> None:
    """C9 — 늙은 신호로 30초짜리 분석을 태우면 그것이 곧 예산 초과다."""
    looked: list[str] = []
    _run(
        [_Event("0xa", "BTCUSDT", "long", "increase", NOW - timedelta(minutes=90))],
        loader=lambda symbol, timeframe: looked.append(symbol) or {},
        max_latency_minutes=30,
    )
    assert looked == [], "지연 초과 신호에 분석을 조회했다"


def test_signal_within_the_cap_is_evaluated() -> None:
    looked: list[str] = []
    result = _run(
        [_Event("0xa", "BTCUSDT", "long", "increase", NOW - timedelta(minutes=3))],
        loader=lambda symbol, timeframe: looked.append(symbol) or {},
        max_latency_minutes=30,
    )
    assert looked == ["BTCUSDT"]
    assert result["evaluated"] == 1


@pytest.mark.parametrize(
    ("direction", "whale_price", "entry_price", "expected"),
    [
        # 롱: 값이 오르면 고래보다 나쁘게 잡는다 → 불리
        (Direction.long, 100.0, 101.0, 1.0),
        # 롱: 값이 내리면 고래보다 싸게 잡는다 → 유리 = 0
        (Direction.long, 100.0, 99.0, 0.0),
        # 숏은 방향이 반대다
        (Direction.short, 100.0, 99.0, 1.0),
        (Direction.short, 100.0, 101.0, 0.0),
    ],
)
def test_drift_measures_only_the_adverse_direction(direction, whale_price, entry_price, expected) -> None:
    """고래보다 싸게 잡은 것을 막을 이유가 없다."""
    drift = whale_follow.price_drift(whale_price=whale_price, entry_price=entry_price, direction=direction, stop_distance=4.0)
    assert drift["adverse_abs"] == pytest.approx(expected)


def test_drift_is_measured_against_the_stop_distance() -> None:
    """스톱 1% 짜리의 0.5% 이탈과 스톱 8% 짜리의 0.5% 이탈은 다른 사건이다."""
    tight = whale_follow.price_drift(whale_price=100.0, entry_price=100.5, direction=Direction.long, stop_distance=1.0)
    wide = whale_follow.price_drift(whale_price=100.0, entry_price=100.5, direction=Direction.long, stop_distance=8.0)
    assert tight["pct_of_stop"] == pytest.approx(50.0)
    assert wide["pct_of_stop"] == pytest.approx(6.25)
    # 절대 이탈은 같다 — 그래서 절대 %로 재면 안 된다.
    assert tight["adverse_pct"] == wide["adverse_pct"]


def test_five_minutes_but_three_percent_is_a_different_trade() -> None:
    """**WO §7-2 항목 4의 문장을 그대로 고정한다.** 지연이 짧아도 이탈이 크면 거부다."""
    signal = {
        "address": "0xa",
        "symbol": "BTCUSDT",
        "direction": Direction.long,
        "event_at": NOW - timedelta(minutes=5),
        "whale_price": 100.0,
        "signal_age_seconds": 300.0,
    }

    calls = {}

    def simulate(symbol, timeframe, direction, entry_price):
        calls["entry_price"] = entry_price
        return {"action_plan": {"invalidation": 97.0}}

    verdict = whale_follow.evaluate_signal(
        _Repo([]),
        Settings(),
        signal,
        analysis_loader=lambda symbol, timeframe: _analysis(103.0),
        simulation_loader=simulate,
        now=NOW,
        max_drift_pct_of_stop=25.0,
    )
    assert verdict["opened"] is False
    assert verdict["reason_code"] == whale_follow.REASON_DRIFT
    # 시뮬레이션도 현재가 기준으로 걸렸다 — 확정봉 종가로 걸면 무효화선이 엉뚱해진다.
    assert calls["entry_price"] == pytest.approx(103.0)


def test_entry_is_refused_when_the_whale_fill_price_is_unknown() -> None:
    """금지 — 상한 없이 진입. 기준선이 없으면 이탈 상한을 걸 수 없다."""
    signal = {
        "address": "0xa",
        "symbol": "BTCUSDT",
        "direction": Direction.long,
        "event_at": NOW,
        "whale_price": None,
        "signal_age_seconds": 0.0,
    }
    verdict = whale_follow.evaluate_signal(
        _Repo([]),
        Settings(),
        signal,
        analysis_loader=lambda symbol, timeframe: _analysis(100.0),
        simulation_loader=lambda *args: {"action_plan": {"invalidation": 97.0}},
        now=NOW,
    )
    assert verdict["opened"] is False
    assert verdict["reason_code"] == whale_follow.REASON_WHALE_PRICE_UNKNOWN


def test_caps_are_reported_with_the_run() -> None:
    """상한 값이 결과에 없으면 "무엇을 기준으로 걸렀나"를 되짚을 수 없다."""
    result = _run([], max_latency_minutes=7, max_drift_pct_of_stop=13.0)
    assert result["caps"] == {"max_latency_minutes": 7, "max_drift_pct_of_stop": 13.0}


def test_rejections_are_queryable_by_reason_code() -> None:
    """7-2 항목 5 · 7-4 항목 5 — 사유 문자열만 남기면 분포를 세지 못한다."""
    summary = whale_follow.rejection_summary(
        [
            {"reason_code": whale_follow.REASON_LATENCY},
            {"reason_code": whale_follow.REASON_LATENCY},
            {"reason_code": whale_follow.REASON_DRIFT},
            {"reason": "코드 없음"},
        ]
    )
    assert summary["by_reason"] == {whale_follow.REASON_LATENCY: 2, whale_follow.REASON_DRIFT: 1}
    assert summary["uncoded"] == 1
    assert whale_follow.REASON_REENTRY_LOCK in summary["zero_counts"]


def test_entry_records_the_caps_it_passed() -> None:
    """상한을 통과했다는 사실과 그 값이 원장에 남는다."""
    trade = whale_follow.open_follow_trade(_candidate(), now=NOW)
    evidence = trade.entry_evidence
    assert evidence["signal_to_entry_seconds"] == pytest.approx(720.0)
    assert evidence["price_drift_pct_of_stop"] == pytest.approx(7.69, abs=0.01)
    assert evidence["whale_price"] == 103.0
    # 진입가의 **실제 나이**다. 기준이 분석 조회 시각(`as_of`)이므로 마크가가 얼마나
    # 묵었는지를 그대로 낸다 — 실측 2~3초. 이름이 뜻과 어긋나면 7-4 의 상한 적정성 판정이
    # 흔들린다.
    assert evidence["price_age_seconds"] == pytest.approx(60.0)
    assert evidence["price_as_of"] is not None
    assert "확정봉 종가가 아니다" in evidence["entry_price_note"]
    assert evidence["qualification"] == fe.QUALIFICATION_FOLLOW
    assert evidence["win_pct"] == 64.9
    assert "실주문이 아닌" in evidence["note"]


def test_latency_is_measured_from_the_decision_clock_not_the_bar() -> None:
    """확정봉 timestamp 는 봉이 열린 시각이라 체결보다 앞설 수 있다.

    그것으로 재고 0 으로 누르면 "지연 없음"이라는 거짓이 원장에 남는다 — Phase 6 실측에서
    실제로 0.0초가 찍혔다. 지연은 엔진이 판단한 벽시계 기준이어야 한다.
    """
    signal = {**_candidate()["signal"], "event_at": NOW - timedelta(minutes=25)}
    trade = whale_follow.open_follow_trade(_candidate(signal=signal), now=NOW)
    assert (trade.entry_evidence or {})["signal_to_entry_seconds"] == pytest.approx(1500.0)


# ── 게이트 범위 · 예산 (변경 없음) ─────────────────────────────────────


def test_direction_gates_are_excluded_from_the_gate_scope() -> None:
    """이 트랙의 가설은 고래 신호가 방향 판단을 대체한다는 것이다."""
    source = (REPO_ROOT / "backend/app/paper/whale_follow.py").read_text(encoding="utf-8")
    gates = source.split("def _safety_gates")[1].split("\ndef ")[0]
    for excluded in ("confirmed_stance", "signature_gate", "regime_gate", "risk_reward", "checklist"):
        assert f'"{excluded}"' not in gates, f"방향 판단 게이트가 적용 범위에 들어왔다: {excluded}"
    for kept in ("freshness", "liquidation_safety", "action_levels", "invalidation_hygiene"):
        assert f'"{kept}"' in gates, f"안전 게이트가 빠졌다: {kept}"


def test_exit_does_not_reintroduce_stance_judgement() -> None:
    """진입에서 뺀 스탠스를 청산에서 되살리면 방향 판단이 뒷문으로 들어온다."""
    source = (REPO_ROOT / "backend/app/paper/whale_follow.py").read_text(encoding="utf-8")
    exits = source.split("def run_exits")[1].split("\ndef _distribution")[0]
    assert "stance_state={}" in exits


def test_analysis_lookups_are_capped_per_run() -> None:
    """C9 — 진입 상한만으로는 부족하다. 전부 거부되면 분석 조회가 무제한이 된다."""
    assert whale_follow.MAX_EVALUATIONS_PER_RUN <= 5
    assert whale_follow.MAX_EXIT_EVALUATIONS_PER_RUN <= 10

    events = [_Event("0xa", f"SYM{index}USDT", "long", "increase", NOW, size_usd=1000.0 * index) for index in range(1, 9)]
    loaded: list[str] = []
    result = _run(events, loader=lambda symbol, timeframe: loaded.append(symbol) or {})

    assert len(loaded) <= whale_follow.MAX_EVALUATIONS_PER_RUN
    assert result["evaluated"] <= whale_follow.MAX_EVALUATIONS_PER_RUN
    # C10 — 잘린 신호가 조용히 사라지지 않는다.
    assert result["rejection_summary"]["by_reason"].get(whale_follow.REASON_EVALUATION_CAP)


def test_no_eligible_wallet_means_no_entry_and_says_so() -> None:
    """7-1 항목 4 — 통과자 0명이면 멈춘다. 기준을 낮추지 않는다."""
    result = whale_follow.run_entries(_Repo([]), object(), eligible={}, analysis_loader=lambda *args: {}, simulation_loader=lambda *args: {}, now=NOW)
    assert result["opened"] == 0
    assert "기준을 낮추지 않는다" in result["reason"]


def test_track_is_registered_with_a_separate_ledger() -> None:
    """C2 — 크립토 트랙 원장에 기입하지 않는다."""
    from app.validation.sample_viability import TRACK_SAMPLE_SPECS

    spec = TRACK_SAMPLE_SPECS["whale_follow"]
    assert "whale_follow_trades" in spec.entry_sql
    assert "paper_trades" not in spec.entry_sql
    assert "paper_trades" not in spec.scored_sql
    crypto = TRACK_SAMPLE_SPECS["crypto"]
    assert "whale_follow" not in crypto.entry_sql, "크립토 트랙이 고래 거래를 세고 있다"


def test_follow_engine_never_writes_to_the_crypto_ledger() -> None:
    """C2 — grep 증명. 추종 엔진에 `upsert_paper_trade` 호출이 없다."""
    source = (REPO_ROOT / "backend/app/paper/whale_follow.py").read_text(encoding="utf-8")
    assert "upsert_paper_trade" not in source
    assert "upsert_whale_follow_trade" in source


# ── 7-3 알림 ──────────────────────────────────────────────────────────


def _trade(*, address: str = "0xa", status: str = "open", entry_at: datetime | None = None, **signal_overrides) -> PaperTrade:
    candidate = _candidate()
    trade = whale_follow.open_follow_trade(_candidate(signal={**candidate["signal"], "address": address, **signal_overrides}), now=NOW)
    update: dict = {"id": uuid4(), "status": status}
    if status == "closed":
        update.update({"net_pnl_usdt": -1.83, "exit_reason": "invalidation_breach"})
    if entry_at is not None:
        update["entry_at"] = entry_at
    return trade.model_copy(update=update)


def test_rule_is_registered_and_distinct_from_whale_entry() -> None:
    assert whale_follow_alerts.WHALE_FOLLOW_RULE_ID == "whale_follow_entry"
    assert whale_follow_alerts.WHALE_FOLLOW_RULE_ID != "whale_entry"
    assert whale_follow_alerts.WHALE_FOLLOW_RULE_ID in delivery_gate.PUSH_ALLOWED_RULES


def test_identity_does_not_depend_on_batch_content() -> None:
    """C7 — 배치 내용이 키에 들어간 것이 스팸 사고의 기전이었다."""
    first = whale_follow_alerts.alert_identity(address="0xA", symbol="btcusdt", direction="long", phase="opened")
    second = whale_follow_alerts.alert_identity(address="0xa", symbol="BTCUSDT", direction="long", phase="opened")
    assert first == second


def test_state_key_is_stable_across_repeated_entries() -> None:
    keys = {whale_follow_alerts.alert_identity(address="0xa", symbol="BTCUSDT", direction="long", phase="opened") for _ in range(5)}
    assert len(keys) == 1


def test_per_run_cap_blocks_the_excess() -> None:
    trades = [_trade(address=f"0x{index}") for index in range(9)]
    result = whale_follow_alerts.build_candidates(trades, now=NOW, per_run_limit=5)
    assert len(result["candidates"]) == 5
    assert any(item.get("cap") == "per_run" for item in result["blocked"])


def test_per_wallet_hourly_cap_blocks_the_excess() -> None:
    recent = [_trade(address="0xa", entry_at=NOW - timedelta(minutes=10)) for _ in range(3)]
    result = whale_follow_alerts.build_candidates([_trade(address="0xa")], now=NOW, recent_trades=recent, per_wallet_hourly_limit=3)
    assert result["candidates"] == []
    assert any(item.get("cap") == "per_wallet_hourly" for item in result["blocked"])


def test_hourly_cap_ignores_trades_outside_the_window() -> None:
    recent = [_trade(address="0xa", entry_at=NOW - timedelta(hours=5)) for _ in range(9)]
    result = whale_follow_alerts.build_candidates([_trade(address="0xa")], now=NOW, recent_trades=recent, per_wallet_hourly_limit=3)
    assert len(result["candidates"]) == 1


def test_hourly_cap_does_not_count_the_trade_being_evaluated() -> None:
    """호출부가 원장 전체를 넘긴다 — 그러면 이 거래가 자기 자신을 세어 상한이 1 작아진다."""
    trade = _trade(address="0xa", entry_at=NOW - timedelta(minutes=1))
    prior = [_trade(address="0xa", entry_at=NOW - timedelta(minutes=10)) for _ in range(2)]
    result = whale_follow_alerts.build_candidates([trade], now=NOW, recent_trades=[*prior, trade], per_wallet_hourly_limit=3)
    assert len(result["candidates"]) == 1, "평가 중인 거래를 기준선에 넣어 상한이 조기 발동했다"


def test_only_opened_and_closed_are_sendable() -> None:
    assert whale_follow_alerts.SENDABLE_PHASES == ("opened", "closed")
    result = whale_follow_alerts.build_candidates([_trade(status="partial")], now=NOW)
    assert result["candidates"] == []
    assert "발송 대상 단계가 아니다" in result["blocked"][0]["reason"]


def test_message_reads_the_qualification_rule_back(regen=None) -> None:
    """**7-3 수용 기준** — 승률·N·유형·지연·이탈이 본문에 있다.

    Phase 6 은 모든 건이 `미검증 · 승격 아님 · CI 하한 35.9%` 로 똑같이 찍혀 라벨이 정보를
    주지 못했다. 자격 규칙을 본문에서 되읽을 수 있어야 한다.
    """
    body = whale_follow_alerts.format_message(_trade(), phase="opened")
    assert "승률 64.9%" in body and "N=37" in body
    assert "unclassified" in body
    assert "진입" in body and "무효화" in body
    assert "체결→진입" in body and "이탈" in body
    assert "미검증 추종 자격" in body
    assert "실주문이 아닌" in body


def test_closed_alert_carries_the_result() -> None:
    """7-3 항목 2 — 진입과 청산이 짝으로 보여야 한다. 결과 없는 청산 알림은 반쪽이다."""
    body = whale_follow_alerts.format_message(_trade(status="closed"), phase="closed")
    assert "청산" in body
    assert "-1.83 USDT" in body
    assert "invalidation_breach" in body


def test_opened_and_closed_share_a_pair_key() -> None:
    opened = whale_follow_alerts.build_candidates([_trade()], now=NOW)["candidates"][0]
    closed = whale_follow_alerts.build_candidates([_trade(status="closed")], now=NOW)["candidates"][0]
    assert opened.payload["pair_key"] == closed.payload["pair_key"]
    assert opened.identity != closed.identity, "단계가 구분되지 않으면 쿨다운이 청산 알림을 삼킨다"


def test_missing_drift_is_reported_as_unmeasured_not_zero() -> None:
    """미측정과 0% 이탈은 다른 사건이다."""
    trade = _trade()
    trade = trade.model_copy(update={"entry_evidence": {**(trade.entry_evidence or {}), "price_drift_pct": None}})
    assert "이탈 미측정" in whale_follow_alerts.format_message(trade, phase="opened")


def test_alerts_do_not_bypass_the_gate() -> None:
    """C7 — grep 증명. 알림 모듈에 발송기가 없다."""
    source = (REPO_ROOT / "backend/app/notify/whale_follow_alerts.py").read_text(encoding="utf-8")
    for bypass in ("TelegramSender", "send_telegram", "httpx", "requests"):
        assert bypass not in source, f"관문 우회 경로가 있다: {bypass}"


# ── 7-4 집계 · 분포 ───────────────────────────────────────────────────


def test_performance_keeps_old_qualifications_separate() -> None:
    """자격이 하나로 줄었어도 Phase 6 행과 섞지 않는다 — 문턱이 다른 표본이다."""
    new_row = _trade()
    old_row = new_row.model_copy(update={"id": uuid4(), "entry_evidence": {**(new_row.entry_evidence or {}), "qualification": "observation"}})
    buckets = whale_follow.performance_by_qualification([new_row, old_row])["buckets"]
    assert set(buckets) == {"follow", "observation"}


def test_distributions_refuse_to_invent_numbers_from_an_empty_sample() -> None:
    """C10 — 표본 0에서 분포를 만들어 내지 않는다."""
    assert whale_follow.latency_distribution([])["count"] == 0
    assert "진입 0건" in whale_follow.drift_distribution([])["note"]
    assert whale_follow.performance_by_qualification([])["buckets"] == {}


def test_distributions_report_latency_and_drift() -> None:
    """7-4 항목 2·3 — 상한이 무엇을 걸렀는지, 상한 값이 적정한지 읽는 근거."""
    trades = [_trade(), _trade()]
    latency = whale_follow.latency_distribution(trades)
    drift = whale_follow.drift_distribution(trades)
    assert latency["count"] == 2 and latency["unit"] == "minutes"
    assert latency["median"] == pytest.approx(12.0)
    assert drift["unit"] == "pct_of_stop"
    assert drift["median"] == pytest.approx(7.69, abs=0.01)


def test_profit_factor_is_absent_rather_than_infinite() -> None:
    """손실 0 에서 무한대를 적지 않는다."""
    winner = _trade(status="closed").model_copy(update={"net_pnl_usdt": 5.0})
    bucket = whale_follow.performance_by_qualification([winner])["buckets"]["follow"]
    assert bucket["profit_factor"] is None
    assert bucket["win_pct"] == 100.0


# ── 실행 구조: 체결 구동 (7-2 항목 1) ──────────────────────────────────


def test_follow_engine_runs_on_the_isolated_executor() -> None:
    """C9 — 조회 상한만으로는 슬롯 점유를 막지 못한다. 격리도 함께 건다."""
    from app.worker.manager import _HEAVY_JOBS

    assert "whale_follow_engine" in _HEAVY_JOBS


def test_collect_job_dispatches_the_follow_run_without_awaiting_it() -> None:
    """**수집 잡(30초)을 블로킹하면 안 된다.**

    추종 엔진의 분석 조회(최대 3건 × ~30초)가 수집 잡의 예산(30초 × 5 = 150초)을 먹는다.
    그 형태가 정확히 `DISCOVERY-UNBLOCK-01` 의 라이브 장애 기전이었다.
    """
    source = (REPO_ROOT / "backend/app/worker/manager.py").read_text(encoding="utf-8")
    collect = source.split("async def _collect_whales")[1].split("\n    def _dispatch_whale_follow")[0]
    assert "await self._run_whale_follow" not in collect, "수집 잡이 추종 실행을 기다린다"
    assert "_dispatch_whale_follow" in collect

    dispatch = source.split("def _dispatch_whale_follow")[1].split("\n    def _log_whale_follow_result")[0]
    assert "asyncio.create_task" in dispatch
    assert "done()" in dispatch, "중복 실행 가드가 없다"


def test_fill_driven_run_shares_the_job_lock_with_the_scheduled_run() -> None:
    """겹치면 분석 조회가 곱해져 예산이 두 배가 된다(C9)."""
    source = (REPO_ROOT / "backend/app/worker/manager.py").read_text(encoding="utf-8")
    body = source.split("async def _run_whale_follow_on_fill")[1].split("\n    async def ")[0]
    assert 'self._locks["whale_follow_engine"]' in body
    assert "lock.locked()" in body


def test_fresh_signal_check_is_cheap_and_scoped_to_eligible_wallets() -> None:
    """30초마다 지갑 전수 조회를 돌리면 그 자체가 예산 사고다(C9)."""
    source = (REPO_ROOT / "backend/app/services/runtime.py").read_text(encoding="utf-8")
    body = source.split("def whale_follow_has_fresh_signal")[1].split("\ndef ")[0]
    assert "cached_whale_follow_eligibility" in body, "캐시를 쓰지 않고 매번 전수 조회한다"
    assert "ENTRY_EVENTS" in body, "진입 체결이 아닌 이벤트로도 깨운다"


# ── 제약 증명 ──────────────────────────────────────────────────────────


def test_policy_and_direction_layers_are_untouched() -> None:
    """C4·C5 — 사이징·잠금·출구·방향 판정 diff 0줄."""
    diff = subprocess.run(["git", "diff", "origin/main", "--stat", "--", *UNTOUCHABLE], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if diff.returncode != 0:
        pytest.skip("origin/main 을 참조할 수 없는 환경")
    assert diff.stdout.strip() == "", f"C4·C5 위반:\n{diff.stdout}"


def test_promotion_thresholds_are_unchanged() -> None:
    """C3 — **추종 자격과 승격은 별개다.** 28일·N>=30·CI 하한 55% 그대로.

    WO 는 `onchain/service.py:572-574` 를 지목했지만 그 줄은 차트 마커다. 실제 승격 임계는
    `_wallet_review` 안에 있으므로 **문자열로** 고정한다 — 줄 번호는 움직인다.
    """
    from app.backtest import candidate_scoring

    assert candidate_scoring.WHALE_VALIDATION_DAYS == 28
    source = (REPO_ROOT / "backend/app/onchain/service.py").read_text(encoding="utf-8")
    assert "sample_size < 30 or ci_low is None or ci_low < 55.0" in source
    assert "sample_size >= 30 and ci_low is not None and ci_low >= 55.0" in source


def test_promotion_module_does_not_import_follow_eligibility() -> None:
    """두 축이 서로를 참조하지 않는다 — 한쪽을 고쳐도 다른 쪽이 움직이지 않는다."""
    source = (REPO_ROOT / "backend/app/onchain/service.py").read_text(encoding="utf-8")
    assert "follow_eligibility" not in source


def test_no_real_order_path_was_added() -> None:
    """C1 — 봉인. 추종 트랙은 페이퍼다."""
    source = (REPO_ROOT / "backend/app/paper/whale_follow.py").read_text(encoding="utf-8")
    for forbidden in ("place_order", "create_order", "submit_order", "bitget"):
        assert forbidden not in source.lower()
