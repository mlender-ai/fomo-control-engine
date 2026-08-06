"""WO-FCE-OBSERVATION-INTEGRITY-01 Phase 5 — 고래 승률 사후 채점 회귀.

5-1 가용성 판정: **가능**. 리더보드에는 승률 필드가 없지만 `userFillsByTime` 의 `closedPnl` 을
수집기가 이미 저장한다. 실측 2026-08-05: close/reduce 6,382건 **전량(100%)** 에 존재,
지갑 62개 · 표본 20건 이상 33개 · 전체 승률 64.7%.

5-2 그런데 승률을 quality_score 에 넣지 **않는다**. 실측이 반대 근거를 냈다:

    0xd04f9719…  승률  4.3% (n=47)  → 누적 +$2,108,265
    0x77375a8c…  승률 51.1% (n=174) → 누적   −$415,726

승률만으로 뽑으면 비대칭 손익 트레이더를 강등시킨다. 승률은 **사후 채점 지표**다.

고정하는 명제:
1. closed_pnl 은 2단 중첩 payload 에서 꺼낸다 (수집 저장 구조)
2. 표본 미달이면 승률을 발표하지 않는다 (모르면 모른다고 낸다)
3. 승률은 선정 기준이 아니며 화면·알림이 그렇게 고지한다
"""

from __future__ import annotations

from app.onchain.win_rate import MIN_SAMPLE, observed_win_rates, selection_disclosure


def _event(address: str, pnl: float | None, *, event: str = "close", nested: bool = True) -> dict:
    payload: dict = {}
    if pnl is not None:
        payload = {"payload": {"closed_pnl": str(pnl), "raw": {"closedPnl": str(pnl)}}} if nested else {"closed_pnl": str(pnl)}
    return {"wallet_address": address, "event_type": event, "payload": payload}


def test_closed_pnl_is_read_from_the_nested_payload() -> None:
    """저장 구조가 2단 중첩이라 한 겹만 보면 100% 결측으로 보인다(초기 구현에서 실제로 그랬다)."""
    rows = observed_win_rates([_event("0xa", 10.0) for _ in range(MIN_SAMPLE)])
    assert rows["0xa"]["sample_size"] == MIN_SAMPLE
    assert rows["0xa"]["win_rate_pct"] == 100.0


def test_flat_payload_is_also_supported() -> None:
    rows = observed_win_rates([_event("0xa", 10.0, nested=False) for _ in range(MIN_SAMPLE)])
    assert rows["0xa"]["sample_size"] == MIN_SAMPLE


def test_low_sample_does_not_publish_a_win_rate() -> None:
    """표본 부족을 성적으로 발표하지 않는다."""
    rows = observed_win_rates([_event("0xa", 10.0), _event("0xa", -5.0)])
    assert rows["0xa"]["sample_low"] is True
    assert rows["0xa"]["win_rate_pct"] is None
    assert rows["0xa"]["sample_size"] == 2


def test_win_rate_and_profit_are_reported_separately() -> None:
    """실측 반례 재현: 낮은 승률 + 큰 이익. 둘을 하나로 합치면 이 지갑을 잘못 판단한다."""
    events = [_event("0xasym", -100.0) for _ in range(23)] + [_event("0xasym", 5000.0)]
    rows = observed_win_rates(events)

    assert rows["0xasym"]["win_rate_pct"] < 10
    assert rows["0xasym"]["profitable"] is True
    assert rows["0xasym"]["closed_pnl_usd"] > 0


def test_high_win_rate_can_still_lose_money() -> None:
    events = [_event("0xchop", 1.0) for _ in range(20)] + [_event("0xchop", -500.0) for _ in range(5)]
    rows = observed_win_rates(events)

    assert rows["0xchop"]["win_rate_pct"] == 80.0
    assert rows["0xchop"]["profitable"] is False


def test_non_closing_events_are_ignored() -> None:
    """increase/open 은 실현 손익이 없다 — 승률 분모에 들어가면 안 된다."""
    events = [_event("0xa", 10.0, event="increase") for _ in range(30)]
    assert observed_win_rates(events) == {}


def test_missing_pnl_is_skipped_not_counted_as_loss() -> None:
    events = [_event("0xa", None) for _ in range(30)]
    assert observed_win_rates(events) == {}


def test_pydantic_style_events_are_accepted() -> None:
    """레포는 dict 가 아니라 모델을 준다 — 접근자가 둘 다 받아야 한다."""

    class _Event:
        def __init__(self) -> None:
            self.wallet_address = "0xa"
            self.event = "close"
            self.payload = {"payload": {"closed_pnl": "12.5"}}

    rows = observed_win_rates([_Event() for _ in range(MIN_SAMPLE)])
    assert rows["0xa"]["win_rate_pct"] == 100.0


def test_disclosure_states_the_selection_basis() -> None:
    """5-4: 화면·알림이 '승률로 뽑았다'로 오해되면 안 된다."""
    rows = observed_win_rates([_event("0xa", 10.0) for _ in range(MIN_SAMPLE)])
    disclosure = selection_disclosure(rows)

    assert "quality_score" in disclosure["selection_basis"]
    assert "선정에 사용하지 않음" in disclosure["win_rate_role"]
    assert "사후 채점" in disclosure["label"]
    assert disclosure["closed_samples"] == MIN_SAMPLE
