"""WO-FCE-TRACK-CAPITAL-01 — 4트랙 자본 원장 계약.

`METRIC-TRUTH-01` 이 크립토에서 고친 정의를 트랙 전체로 확장한다. 아래 테스트는 그 정의가
다시 흐려지는 경로를 막는다 — 특히 **묶인 현금을 손실로 부르는 것**을.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from app.validation import track_capital as tc

REPO_ROOT = Path(__file__).resolve().parents[2]

# C5 — 판정·진입 로직은 이 WO 가 건드리지 않는다.
UNTOUCHABLE = ("backend/app/analyst", "backend/app/structure", "backend/app/paper/policy.py")


class _Settings:
    paper_margin_usdt = 100.0
    paper_max_open_positions = 5


def _db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE poly_paper_track (id INTEGER PRIMARY KEY, initial_cash REAL, cash REAL)")
    connection.execute("CREATE TABLE poly_positions (market_id TEXT PRIMARY KEY, cost REAL)")
    connection.execute("CREATE TABLE stock_paper_tracks (market TEXT PRIMARY KEY, initial_cash REAL, cash REAL)")
    connection.execute("CREATE TABLE stock_paper_positions (market TEXT, symbol TEXT, quantity INTEGER, average_price REAL, PRIMARY KEY(market, symbol))")
    connection.execute("CREATE TABLE stock_paper_marks (market TEXT, symbol TEXT, price REAL, PRIMARY KEY(market, symbol))")
    return connection


# ── 1-1 · 시작 자본 출처 ────────────────────────────────────────────────


def test_crypto_capital_comes_from_settings_not_a_constant() -> None:
    """`METRIC_DEFINITIONS.md` §1 — 하드코딩하지 않는다. 설정이 바뀌면 자본도 바뀐다."""
    capital = tc.crypto_capital(_Settings())
    assert capital.amount == 500.0
    assert capital.source == tc.SOURCE_SETTINGS
    assert "paper_margin_usdt" in capital.note


def test_crypto_capital_is_unknown_when_settings_are_missing() -> None:
    class _Empty:
        paper_margin_usdt = 0.0
        paper_max_open_positions = 0

    capital = tc.crypto_capital(_Empty())
    assert capital.amount is None
    assert capital.known is False


def test_whale_follow_capital_is_unknown_until_declared() -> None:
    """선언이 없으면 미상이다 — 강제하지 않는 상한으로 자본을 만들지 않는다(C7).

    WO-FCE-DEFAULTS-01 1-1 이 500 을 선언했고, 그 선언은 상한 강제와 함께 온다. 선언이
    없는 상태(설정 미제공·0)에서는 여전히 미상이며 그것이 원복 지점이다.
    """
    capital = tc.whale_follow_capital()
    assert capital.amount is None
    assert capital.source == tc.SOURCE_UNKNOWN
    assert "선언되지 않았다" in capital.note


def test_ledger_capital_reads_the_recorded_value() -> None:
    connection = _db()
    connection.execute("INSERT INTO poly_paper_track (id, initial_cash, cash) VALUES (1, 10000.0, 8416.88)")
    capital = tc.ledger_capital(connection, table="poly_paper_track")
    assert capital.amount == 10000.0
    assert capital.source == tc.SOURCE_LEDGER


def test_missing_ledger_row_is_unknown_not_zero() -> None:
    capital = tc.ledger_capital(_db(), table="poly_paper_track")
    assert capital.amount is None
    assert "행이 없다" in capital.note


# ── 1-2 · 묶인 현금을 손실로 부르지 않는다 (핵심) ───────────────────────


def test_deployed_cash_is_not_realized_loss() -> None:
    """**이 WO 의 핵심 오류.** `cash − initial_cash` 를 실현으로 쓰면 안 된다.

    실측: 폴리 현금 부족분 1,583.1227 과 포지션 원가 1,583.1227 이 소수점까지 같았다 —
    아무것도 실현되지 않았는데 −15.83% 로 찍혔다.
    """
    connection = _db()
    connection.execute("INSERT INTO poly_paper_track (id, initial_cash, cash) VALUES (1, 10000.0, 8416.8773)")
    connection.execute("INSERT INTO poly_positions (market_id, cost) VALUES ('m', 1583.1227)")
    cost, marks = tc._poly_position_values(connection)
    flows = tc._cash_track_pnl(connection, table="poly_paper_track", cost_basis=cost, mark_value=marks)
    assert flows["realized"] == pytest.approx(0.0, abs=1e-6), "묶인 현금을 손실로 셌다"
    assert flows["deployed_capital"] == pytest.approx(1583.1227)


def test_fees_survive_the_decomposition() -> None:
    """실현이 0 이 되는 것이 아니라 **수수료만 남아야** 한다."""
    connection = _db()
    connection.execute("INSERT INTO stock_paper_tracks (market, initial_cash, cash) VALUES ('KR', 100000000.0, 95598339.85)")
    connection.execute("INSERT INTO stock_paper_positions (market, symbol, quantity, average_price) VALUES ('KR', '005930', 100, 44010.0)")
    connection.execute("INSERT INTO stock_paper_marks (market, symbol, price) VALUES ('KR', '005930', 44760.0)")
    cost, marks = tc._stock_position_values(connection, "KR")
    flows = tc._cash_track_pnl(connection, table="stock_paper_tracks", where="market=?", params=("KR",), cost_basis=cost, mark_value=marks)
    assert flows["realized"] == pytest.approx(-660.15, abs=0.01), "수수료가 사라지거나 부풀었다"
    assert flows["unrealized"] == pytest.approx(75000.0)
    assert flows["nav"] == pytest.approx(100074339.85, abs=0.01)


def test_partial_marks_do_not_produce_a_valuation() -> None:
    """일부 마크만 있으면 평가액을 만들지 않는다 — 부분 평가액은 NAV 를 왜곡한다."""
    connection = _db()
    connection.execute("INSERT INTO stock_paper_positions (market, symbol, quantity, average_price) VALUES ('KR', 'A', 10, 100.0)")
    connection.execute("INSERT INTO stock_paper_positions (market, symbol, quantity, average_price) VALUES ('KR', 'B', 10, 200.0)")
    connection.execute("INSERT INTO stock_paper_marks (market, symbol, price) VALUES ('KR', 'A', 150.0)")
    cost, marks = tc._stock_position_values(connection, "KR")
    assert cost == pytest.approx(3000.0)
    assert marks is None


def test_unrealized_is_never_folded_into_realized() -> None:
    """C4 — 두 값이 따로 나온다."""
    connection = _db()
    connection.execute("INSERT INTO stock_paper_tracks (market, initial_cash, cash) VALUES ('US', 100000.0, 97991.68)")
    connection.execute("INSERT INTO stock_paper_positions (market, symbol, quantity, average_price) VALUES ('US', 'NVDA', 10, 201.214)")
    connection.execute("INSERT INTO stock_paper_marks (market, symbol, price) VALUES ('US', 'NVDA', 206.346)")
    cost, marks = tc._stock_position_values(connection, "US")
    flows = tc._cash_track_pnl(connection, table="stock_paper_tracks", where="market=?", params=("US",), cost_basis=cost, mark_value=marks)
    assert flows["realized"] != flows["unrealized"]
    assert "합산하지 않는다" in flows["unrealized_note"]


# ── 1-2 · 자본 미상이면 수익률을 만들지 않는다 ─────────────────────────


def test_unknown_capital_yields_no_return_pct() -> None:
    """C1 — 자본 미상이면 `None` 이다. 대표값은 금액이다."""
    connection = _db()
    connection.execute("CREATE TABLE whale_follow_trades (id TEXT, entry_bar_at TEXT, status TEXT, payload TEXT)")
    connection.execute("INSERT INTO whale_follow_trades VALUES ('1','2026-08-01T00:00:00Z','closed','{\"status\":\"closed\",\"net_pnl_usdt\":-8.6355}')")
    block = tc.track_capital(connection, _Settings(), "whale_follow")
    assert block["starting_capital"] is None
    assert block["return_on_capital_pct"] is None
    assert "미산출" in block["return_note"]
    assert block["realized_pnl"] == pytest.approx(-8.6355)


def test_unpriced_positions_block_nav_not_the_realized_capital() -> None:
    """평가 불가 포지션이 있어도 **실현 기준 자본은 말할 수 있다.**

    이전에는 `current_capital` 을 통째로 `None` 으로 만들어 막았다. 그 가드가 막으려던 것은
    `10,000` 이라는 숫자 자체가 아니라 **그 숫자가 NAV 로 읽히는 것**이었다 — 1,583 USDC 가
    값 모르는 포지션에 묶여 있는데 "원금 그대로"로 보이는 것.

    WO-FCE-REPORT-DEFECTS-01 7-1 이 자본을 실현 기준으로 통일하면서 그 가드를 **숨김에서
    라벨로** 옮겼다. 폴리는 실현이 정확히 0 이고, 그것은 미상이 아니라 아는 값이다.
    대신 NAV 는 만들지 않고 묶인 자본을 고지한다 — 모르는 것은 여전히 모른다고 적는다(C7).
    """
    connection = _db()
    connection.execute("INSERT INTO poly_paper_track (id, initial_cash, cash) VALUES (1, 10000.0, 8416.8773)")
    connection.execute("INSERT INTO poly_positions (market_id, cost) VALUES ('m', 1583.1227)")
    block = tc.track_capital(connection, _Settings(), "poly")

    assert block["realized_pnl"] == pytest.approx(0.0), "실현은 정확히 0 이다 — 아무것도 청산되지 않았다"
    assert block["current_capital"] == pytest.approx(10000.0)
    assert block["current_capital_basis"] == "realized"
    # **NAV 는 여전히 만들지 않는다.** 평가액을 모르면 모르는 것이다.
    assert block["nav"] is None
    assert block["unpriced_positions"] is True
    assert "묶여 있다" in block["current_capital_note"]


def test_unpriced_position_warning_reaches_the_report() -> None:
    """숫자만 고치고 고지가 사라지면 가드를 옮긴 것이 아니라 없앤 것이다."""
    from app.notify import daily_report

    connection = _db()
    connection.execute("INSERT INTO poly_paper_track (id, initial_cash, cash) VALUES (1, 10000.0, 8416.8773)")
    connection.execute("INSERT INTO poly_positions (market_id, cost) VALUES ('m', 1583.1227)")
    lines = daily_report.capital_lines("poly", tc.track_capital(connection, _Settings(), "poly"))

    assert any("평가 불가 포지션" in line and "1,583.12" in line for line in lines), lines


def test_flat_track_can_state_current_capital() -> None:
    """포지션이 없으면 `시작 + 실현` 이 현재 자본이다 — 그때는 부를 수 있다."""
    connection = _db()
    connection.execute("INSERT INTO poly_paper_track (id, initial_cash, cash) VALUES (1, 10000.0, 9500.0)")
    block = tc.track_capital(connection, _Settings(), "poly")
    assert block["current_capital"] == pytest.approx(9500.0)
    assert block["current_capital_note"] is None


# ── C2 · 트랙 간 합산 금지 ─────────────────────────────────────────────


def test_no_total_is_produced_across_tracks() -> None:
    """통화가 다르고 트랙별 독립 판정이 규정이다."""
    result = tc.all_tracks(_db(), _Settings())
    assert "total" not in result
    assert "합산하지 않는다" in result["no_total"]
    for name, block in result["tracks"].items():
        assert block["currency"] == tc.TRACK_CURRENCY[name]


def test_currencies_are_not_mixed() -> None:
    assert tc.TRACK_CURRENCY["poly"] == "USDC"
    assert tc.TRACK_CURRENCY["stock_kr"] == "KRW"
    assert tc.TRACK_CURRENCY["stock_us"] == "USD"
    assert tc.TRACK_CURRENCY["crypto"] == tc.TRACK_CURRENCY["whale_follow"] == "USDT"


# ── 표본 부족 명시 ─────────────────────────────────────────────────────


def test_small_sample_is_labelled() -> None:
    connection = _db()
    connection.execute("CREATE TABLE paper_trades (id TEXT, entry_bar_at TEXT, status TEXT, payload TEXT)")
    connection.execute("INSERT INTO paper_trades VALUES ('1','2026-08-01T00:00:00Z','closed','{\"status\":\"closed\",\"net_pnl_usdt\":1.0}')")
    block = tc.track_capital(connection, _Settings(), "crypto")
    assert block["sample_sufficient"] is False
    assert "N<30" in block["sample_note"]


# ── 제약 증명 ──────────────────────────────────────────────────────────


def test_module_does_not_write_to_any_ledger() -> None:
    """C6 — 계산 계층에서만 산출한다. 원장을 고치지 않는다."""
    source = (REPO_ROOT / "backend/app/validation/track_capital.py").read_text(encoding="utf-8")
    for mutation in ("INSERT ", "UPDATE ", "DELETE ", "commit()"):
        assert mutation not in source, f"원장 변경 경로가 있다: {mutation}"


def test_judgement_and_entry_layers_are_untouched() -> None:
    """C5 — 판정·진입 로직 diff 0줄."""
    diff = subprocess.run(["git", "diff", "origin/main", "--stat", "--", *UNTOUCHABLE], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if diff.returncode != 0:
        pytest.skip("origin/main 을 참조할 수 없는 환경")
    assert diff.stdout.strip() == "", f"C5 위반:\n{diff.stdout}"


# ── WO-FCE-DEFAULTS-01 1-1 · 선언된 임시 자본 ───────────────────────────


class _Declared:
    paper_margin_usdt = 100.0
    paper_max_open_positions = 5
    whale_follow_starting_capital_usdt = 500.0
    whale_follow_max_open_positions = 5


def test_declared_capital_matches_the_crypto_track() -> None:
    """두 트랙을 같은 자본에서 비교할 수 있어야 한다. 유리하게 잡지 않았다(C8)."""
    settings = _Declared()
    assert tc.whale_follow_capital(settings).amount == tc.crypto_capital(settings).amount == 500.0
    assert tc.whale_follow_capital(settings).source == tc.SOURCE_DECLARED


def test_declared_capital_is_labelled_provisional() -> None:
    """C5 — 확정값처럼 보이면 안 된다."""
    note = tc.whale_follow_capital(_Declared()).note
    assert "임시값" in note
    assert "동시 보유 상한 5건 강제" in note


def test_zero_declaration_reverts_to_unknown() -> None:
    """C4 — 설정 한 값으로 되돌린다."""

    class _Off(_Declared):
        whale_follow_starting_capital_usdt = 0.0

    capital = tc.whale_follow_capital(_Off())
    assert capital.amount is None
    assert capital.source == tc.SOURCE_UNKNOWN


def test_capital_without_an_enforced_cap_says_so() -> None:
    """상한 없이 자본만 선언하면 그 자본이 거짓이 된다 — 숨기지 않는다."""

    class _NoCap(_Declared):
        whale_follow_max_open_positions = 0

    note = tc.whale_follow_capital(_NoCap()).note
    assert "강제되지 않는다" in note
    assert "실제 노출이 자본을 넘을 수 있다" in note
