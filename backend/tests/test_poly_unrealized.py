"""WO-FCE-OBSERVATION-INTEGRITY-01 Phase 4 — 폴리 미실현·만기 분포 회귀.

실측 2026-08-05: 보유 9건 중 **8건이 2027-01-01 만기** — 검증 종료(08-19)보다 5개월 뒤다.
검증 기간 내 만기 도래 보유 시장 0건(1건은 08-01 에 이미 정산). 정산 로직은 정상 동작한다.

즉 **폴리는 이번 검증에서 정산 표본을 만들 수 없다.** 그래서 미실현을 낸다.

고정하는 명제:
1. 미실현은 정산과 절대 합산되지 않는다 (C3 — 없는 성적을 있는 것처럼 보이면 안 된다)
2. NO 포지션의 현재가는 1 - YES 확률이다
3. 검증 기간 내 정산 예정 수를 숨기지 않는다 — 0이면 0이라고 말한다
"""

from __future__ import annotations

from app.poly_paper.store import _position_view

ENDS_AT = "2026-08-19T00:00:00+00:00"


def _row(**overrides) -> dict:
    base = {
        "market_id": "1",
        "direction": "YES",
        "shares": 100.0,
        "cost": 40.0,
        "status": "open",
        "market_probability": 0.5,
        "end_at": "2027-01-01T00:00:00+00:00",
        "pnl": None,
    }
    base.update(overrides)
    return base


def test_yes_position_unrealized_uses_market_probability() -> None:
    positions, unrealized, _ = _position_view([_row()], validation_ends_at=ENDS_AT)

    assert positions[0]["unrealized_value"] == 50.0  # 100주 × 0.5
    assert positions[0]["unrealized_pnl"] == 10.0
    assert positions[0]["unrealized_return_pct"] == 25.0
    assert unrealized["pnl"] == 10.0


def test_no_position_is_priced_as_one_minus_probability() -> None:
    """NO 를 YES 확률로 평가하면 손익 부호가 뒤집힌다."""
    positions, _, _ = _position_view([_row(direction="NO", market_probability=0.9, shares=100.0, cost=40.0)], validation_ends_at=ENDS_AT)

    assert positions[0]["unrealized_value"] == 10.0  # 100 × (1 - 0.9)
    assert positions[0]["unrealized_pnl"] == -30.0


def test_unrealized_is_never_merged_with_settlement() -> None:
    """정산된 포지션은 미실현에 들어가지 않는다 — 이중 계상이자 거짓 성적이다."""
    rows = [_row(status="resolved", pnl=-42.5, market_probability=0.0), _row(market_id="2")]

    positions, unrealized, _ = _position_view(rows, validation_ends_at=ENDS_AT)

    assert positions[0]["unrealized_pnl"] is None, "정산분에 미실현을 붙이면 안 된다"
    assert unrealized["open_positions"] == 1
    assert unrealized["cost"] == 40.0
    assert unrealized["is_settled"] is False
    assert "합산하지 않습니다" in unrealized["note"]


def test_expiry_summary_reports_zero_honestly() -> None:
    """실측 재현: 보유 전부 2027-01-01 만기 → 검증 기간 내 정산 예정 0건."""
    rows = [_row(market_id=str(index)) for index in range(8)]

    _, _, expiry = _position_view(rows, validation_ends_at=ENDS_AT)

    assert expiry["settling_within_validation"] == 0
    assert expiry["sample_possible"] is False, "표본을 만들 수 없으면 그렇다고 말해야 한다"
    assert expiry["nearest_end_at"].startswith("2027-01-01")
    assert "정산 대기 8건" in expiry["label"]


def test_expiry_counts_markets_inside_the_window() -> None:
    rows = [_row(market_id="1", end_at="2026-08-10T00:00:00+00:00"), _row(market_id="2")]

    positions, _, expiry = _position_view(rows, validation_ends_at=ENDS_AT)

    assert expiry["settling_within_validation"] == 1
    assert expiry["sample_possible"] is True
    assert positions[0]["settles_within_validation"] is True
    assert positions[1]["settles_within_validation"] is False


def test_missing_probability_does_not_fabricate_a_value() -> None:
    """시장가가 없으면 미실현도 없다 — 지어내지 않는다."""
    positions, unrealized, _ = _position_view([_row(market_probability=None)], validation_ends_at=ENDS_AT)

    assert positions[0]["unrealized_pnl"] is None
    assert unrealized["cost"] == 0.0


def test_no_validation_deadline_is_handled() -> None:
    _, _, expiry = _position_view([_row()], validation_ends_at=None)
    assert expiry["settling_within_validation"] == 0
    assert expiry["validation_ends_at"] is None
