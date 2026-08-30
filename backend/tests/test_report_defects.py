"""WO-FCE-REPORT-DEFECTS-01 — 리포트 숫자들이 서로 모순되지 않는다.

실측 2026-08-29 리포트가 네 가지를 동시에 말했다:

| | 증상 |
| --- | --- |
| D1 | `100,000,000 → 100,074,340 (-0.00%)` — **자본은 늘고 수익률은 음수** |
| D2 | `대상 3지갑` 에 자격 탈락 지갑(승률 51.3%)이 들어 있다 |
| D3 | `표본 30건 이상 지갑 65개` 인데 실제 계산 문턱은 20 이었다 |
| D4 | 정지 트랙에 미실현이 쌓이고 그 값이 자본에 섞였다 |

**전부 표시·집계 결함이고 판정 로직은 정확했다**(C1). 이 파일은 그 사실을 고정한다.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from app.notify import daily_report as dr
from app.onchain import follow_eligibility as fe
from app.onchain import participant_type
from app.onchain import win_rate
from app.validation import track_capital as tc

REPO_ROOT = Path(__file__).resolve().parents[2]

# C1·C4·C5 — 판정·진입·출구·자격 기준·MDD 임계는 한 줄도 바뀌지 않는다.
UNTOUCHABLE = (
    "backend/app/paper/policy.py",
    "backend/app/paper/whale_follow.py",
    "backend/app/analyst",
    "backend/app/structure",
    "backend/app/stock_paper/policy.py",
)


class _Settings:
    database_url = "sqlite:///:memory:"
    paper_margin_usdt = 100.0
    paper_max_open_positions = 5
    whale_follow_starting_capital_usdt = 500.0
    whale_follow_max_open_positions = 5
    stock_paper_hold_queued_orders = False


def _db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE stock_paper_tracks (market TEXT, initial_cash REAL, cash REAL, status TEXT, stop_reason TEXT)")
    connection.execute("CREATE TABLE stock_paper_positions (market TEXT, symbol TEXT, quantity INTEGER, average_price REAL)")
    connection.execute("CREATE TABLE stock_paper_marks (market TEXT, symbol TEXT, price REAL)")
    return connection


def _kr_track(*, cash: float, quantity: int, average: float, mark: float | None) -> sqlite3.Connection:
    """실측 재현 — 시작 1억, 수수료로 −660.15 실현, 보유 포지션 평가익 +75,000."""
    connection = _db()
    connection.execute("INSERT INTO stock_paper_tracks VALUES ('KR', 100000000.0, ?, 'running', NULL)", (cash,))
    connection.execute("INSERT INTO stock_paper_positions VALUES ('KR', '005930', ?, ?)", (quantity, average))
    if mark is not None:
        connection.execute("INSERT INTO stock_paper_marks VALUES ('KR', '005930', ?)", (mark,))
    return connection


# ── D1 · 자본과 수익률이 같은 기준이다 (7-1) ────────────────────────────


def _measured_kr() -> dict:
    # 현금 = 1억 − 4,401,000(원가) − 660.15(수수료) · 평가액 = 원가 + 75,000
    return tc.track_capital(_kr_track(cash=95598339.85, quantity=1000, average=4401.0, mark=4476.0), _Settings(), "stock_kr")


def test_the_measured_contradiction_is_gone() -> None:
    """**실측 재현.** `100,074,340 (-0.00%)` — 자본은 늘었는데 수익률이 음수였다."""
    block = _measured_kr()

    assert block["realized_pnl"] == pytest.approx(-660.15)
    assert block["unrealized_pnl"] == pytest.approx(75000.0)
    # 현재 자본은 이제 **실현 기준**이다. 74,340 이 아니라 −660.15 만큼 줄어 있다.
    assert block["current_capital"] == pytest.approx(99999339.85)
    assert block["return_on_capital_pct"] < 0
    # 자본이 줄었으니 수익률도 음수 — 두 수가 같은 것을 말한다.
    assert (block["current_capital"] - block["starting_capital"]) < 0


@pytest.mark.parametrize(
    ("cash", "quantity", "average", "mark"),
    [
        (95598339.85, 1000, 4401.0, 4476.0),  # 실현 −660.15 · 미실현 +75,000 (실측)
        (100500000.0, 0, 0.0, None),  # 실현 +500,000 · 포지션 없음
        (99000000.0, 100, 5000.0, 4000.0),  # 실현 −500,000 · 미실현 −100,000
    ],
)
def test_capital_and_return_share_a_basis(cash: float, quantity: int, average: float, mark: float | None) -> None:
    """**7-1 핵심 수용 기준: 자본이 늘면 수익률도 양수다.**

    미실현이 자본에만 들어가고 수익률에는 안 들어가면 이 부호가 갈린다 — 그것이 D1 이었다.
    """
    block = tc.track_capital(_kr_track(cash=cash, quantity=quantity, average=average, mark=mark), _Settings(), "stock_kr")
    delta = block["current_capital"] - block["starting_capital"]

    assert (delta > 0) == (block["return_on_capital_pct"] > 0), "자본 증감과 수익률 부호가 어긋난다"
    assert (delta < 0) == (block["return_on_capital_pct"] < 0)
    # 분자가 같다는 것을 값으로도 확인한다.
    assert delta == pytest.approx(block["realized_pnl"])


def test_unrealized_is_never_folded_into_capital(monkeypatch) -> None:
    """C3 — 합산해서 하나로 만들지 않는다."""
    block = _measured_kr()
    combined = block["starting_capital"] + block["realized_pnl"] + block["unrealized_pnl"]

    assert block["current_capital"] != pytest.approx(combined)
    # 그 합은 **NAV 라는 다른 이름**으로만 존재한다.
    assert block["nav"] == pytest.approx(combined)
    assert "수익률의 분자가 아니다" in block["nav_note"]


def test_nav_is_a_separate_line_and_labelled(monkeypatch) -> None:
    """같은 줄에 두면 그 줄이 다시 거짓이 된다."""
    lines = dr.capital_lines("stock_kr", _measured_kr())
    capital_line = next(line for line in lines if line.startswith("  자본 "))
    nav_line = next(line for line in lines if line.startswith("  평가 "))

    assert "실현 기준" in capital_line
    assert "100,074,340" not in capital_line, "NAV 가 자본 줄에 남아 있다"
    assert "100,074,340" in nav_line
    assert "수익률 분자 아님" in nav_line


def test_every_track_uses_the_same_basis() -> None:
    """7-1 항목 2 — 트랙마다 다른 기준을 쓰지 않는다."""
    connection = _kr_track(cash=95598339.85, quantity=1000, average=4401.0, mark=4476.0)
    connection.execute("CREATE TABLE paper_trades (id TEXT, entry_bar_at TEXT, status TEXT, payload TEXT)")
    connection.execute("INSERT INTO paper_trades VALUES ('1','2026-08-01T00:00:00Z','closed','{\"status\":\"closed\",\"net_pnl_usdt\":-70.17}')")

    for track in ("crypto", "stock_kr"):
        block = tc.track_capital(connection, _Settings(), track)
        assert block["current_capital_basis"] == "realized", track
        assert block["current_capital"] == pytest.approx(block["starting_capital"] + block["realized_pnl"]), track


def test_screen_and_report_read_the_same_producer() -> None:
    """7-1 항목 3 — 화면과 리포트가 어긋나면 안 된다. 같은 함수를 읽으면 어긋날 수 없다."""
    row = (REPO_ROOT / "dashboard/components/TrackCapitalRow.tsx").read_text(encoding="utf-8")
    assert "capital.current_capital" in row
    assert "capital.return_on_capital_pct" in row
    # 화면이 자기만의 자본식을 만들지 않는다.
    assert "starting_capital +" not in row and "unrealized_pnl +" not in row


# ── D2 · 추종 대상은 자격 통과 목록이다 (7-2) ───────────────────────────


def test_report_reads_eligibility_not_trade_history() -> None:
    """**D2 의 원인은 출처였다.** `performance_by_whale` 은 거래 이력이지 자격이 아니다."""
    source = (REPO_ROOT / "backend/app/notify/daily_report_source.py").read_text(encoding="utf-8")
    # `_paper_metrics` 에도 같은 분기가 있으므로 리포트 조립부를 앵커로 쓴다.
    block = source.split("def build_report")[1]

    assert "follow_report.follow_targets" in block, "자격 정본을 읽지 않는다"
    assert "자격 통과" in block


def test_eligibility_has_one_producer() -> None:
    """두 곳이 각자 계산하면 그것이 곧 두 개의 진실이다."""
    runtime = (REPO_ROOT / "backend/app/services/runtime.py").read_text(encoding="utf-8")
    body = runtime.split("def whale_follow_eligibility()")[1].split("\ndef ")[0]

    assert "follow_report.follow_eligibility_report" in body
    # 계산을 다시 쓰지 않는다 — 위임만 한다.
    assert "whale_sample_sizes" not in body and "follow_status" not in body


def test_the_measured_wallet_is_rejected_by_the_win_rate_bar() -> None:
    """**7-2 항목 3 — `0x1ee7…edf5` 의 자격 상태와 근거 수치.**

    실측: N=39 · 승 20 → 점추정 51.3% → `FOLLOW_MIN_WIN_PCT=55.0` 미달 → **탈락**.
    자격 판정은 정확했다. 목록이 다른 것을 세고 있었다.
    """
    status = fe.follow_status(
        address="0x1ee7edf5",
        sample_size=39,
        wins=20,
        ci_low=35.9,
        estimate={"participant_type": participant_type.TYPE_UNCLASSIFIED, "confidence": 0.0},
    )
    assert status.win_pct == 51.3
    assert status.eligible is False
    assert fe.rejection_reason(status) == fe.REASON_WIN_RATE


def test_lapsed_wallets_keep_their_open_positions() -> None:
    """7-2 항목 4 — 자격 상실은 **신규 진입만** 막는다. 임의 청산하지 않는다(C6)."""
    from app.onchain import follow_report

    source = (REPO_ROOT / "backend/app/paper/whale_follow.py").read_text(encoding="utf-8")
    exits = source.split("def run_exits")[1].split("\ndef _distribution")[0]
    assert "eligible" not in exits, "출구가 자격을 본다 — 자격 상실이 청산 사유가 됐다"

    assert "신규 진입만" in follow_report.follow_targets.__doc__ or "신규 진입만" in follow_report.__doc__


def test_funnel_decomposes_the_rejections() -> None:
    """7-3 항목 3 — "N개 중 3개"만 보이면 그 감소가 기준 탓인지 표본 탓인지 알 수 없다."""
    statuses = {
        "pass": fe.follow_status(address="pass", sample_size=37, wins=24, estimate={"participant_type": participant_type.TYPE_DIRECTIONAL}),
        "win": fe.follow_status(address="win", sample_size=39, wins=20, estimate={"participant_type": participant_type.TYPE_UNCLASSIFIED}),
        "small": fe.follow_status(address="small", sample_size=12, wins=12, estimate={"participant_type": participant_type.TYPE_DIRECTIONAL}),
        "mm": fe.follow_status(address="mm", sample_size=99, wins=99, estimate={"participant_type": participant_type.TYPE_MARKET_MAKER}),
    }
    funnel = fe.funnel(statuses)

    assert funnel["population"] == 4
    assert funnel["eligible"] == 1
    assert funnel["rejected"] == {fe.REASON_EXCLUDED_TYPE: 1, fe.REASON_SAMPLE: 1, fe.REASON_WIN_RATE: 1}
    # 모집단이 무엇인지가 값과 함께 다닌다 — 그것이 D3 의 핵심이다.
    assert "다르다" in funnel["population_note"]


# ── D3 · 리더보드 숫자 (7-3) ────────────────────────────────────────────


def _events(counts: dict[str, tuple[int, int]]) -> list[dict]:
    rows = []
    for address, (total, wins) in counts.items():
        for index in range(total):
            rows.append({"wallet_address": address, "event_type": "close", "payload": {"closed_pnl": 1.0 if index < wins else -1.0}})
    return rows


def test_the_label_threshold_matches_the_counted_threshold() -> None:
    """**실측 라벨이 거짓이었다.** `표본 30건 이상 지갑 65개` 는 실제로 20건 이상이었다.

    `observed_win_rates` 가 자기 기본값 20 을 쓰는데 라벨은 30 을 찍었다.
    """
    events = _events({"0xa": (25, 15), "0xb": (35, 20), "0xc": (15, 10)})

    at_20 = win_rate.selection_disclosure(win_rate.observed_win_rates(events, min_sample=20), min_sample=20)
    at_30 = win_rate.selection_disclosure(win_rate.observed_win_rates(events, min_sample=30), min_sample=30)

    assert at_20["scored_wallets"] == 2
    assert at_30["scored_wallets"] == 1, "문턱을 올렸는데 계수가 따라오지 않는다"
    assert at_30["min_sample"] == 30


def test_leaderboard_line_states_its_aggregation() -> None:
    """7-3 항목 1·4 — 방식 없는 승률은 읽는 사람이 지갑 평균으로 읽는다."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE whale_events (wallet_address TEXT, event_type TEXT, payload TEXT)")
    connection.execute("CREATE TABLE whale_wallets (active INTEGER)")
    connection.execute("INSERT INTO whale_wallets VALUES (1)")
    for row in _events({"0xa": (40, 30), "0xb": (40, 10)}):
        connection.execute("INSERT INTO whale_events VALUES (?,?,?)", (row["wallet_address"], row["event_type"], '{"closed_pnl": 1.0}'))

    from app.notify.content_summary import whale_line

    line = whale_line(connection)
    assert "체결 가중" in line, "집계 방식이 없다"
    assert "다른 모집단" in line, "고래 자신의 승률과 추종 자격 승률이 구분되지 않는다"


def test_fill_weighted_and_wallet_median_are_both_reported() -> None:
    """체결 많은 소수 지갑이 값을 끌고 갈 수 있다. 하나만 보이면 그것을 알 수 없다."""
    # 고빈도 지갑 하나가 90%, 소액 지갑 셋이 20%.
    disclosure = win_rate.selection_disclosure(
        win_rate.observed_win_rates(_events({"0xbig": (900, 810), "0x1": (30, 6), "0x2": (30, 6), "0x3": (30, 6)}), min_sample=30),
        min_sample=30,
    )

    assert disclosure["overall_win_rate_basis"] == "fill_weighted"
    assert disclosure["overall_win_rate_pct"] > 70, "체결 가중은 고빈도 지갑을 따라간다"
    assert disclosure["wallet_median_win_rate_pct"] == 20.0, "지갑 중앙값은 그렇지 않다"


# ── D4 · 정지 트랙 미실현 (7-4) ─────────────────────────────────────────


def test_halted_track_unrealized_is_labelled_separately() -> None:
    """7-4 항목 2 — 정지 중 미실현을 정상 트랙 미실현과 섞지 않는다."""
    block = _measured_kr()
    normal = dr.capital_lines("stock_kr", block)
    halted = dr.capital_lines("stock_kr", block, state={"kind": "halted", "detail": "체결 invariant"})

    assert not any("정지 중 평가액" in line for line in normal)
    assert any("정지 중 평가액" in line and "청산 안 됨" in line for line in halted)


def test_halted_track_capital_still_excludes_unrealized() -> None:
    """정지가 길어질수록 미실현이 커진다. 그것이 자본에 섞이면 트랙이 벌고 있는 것처럼 보인다."""
    block = _measured_kr()
    assert block["current_capital"] < block["starting_capital"], "미실현이 자본을 밀어 올리고 있다"


# ── 제약 증명 ──────────────────────────────────────────────────────────


def test_judgment_and_exit_layers_are_untouched() -> None:
    """C1 — 이 WO 는 표시·집계 계층이다. 판정·진입·출구 diff 0줄."""
    diff = subprocess.run(["git", "diff", "origin/main", "--stat", "--", *UNTOUCHABLE], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if diff.returncode != 0:
        pytest.skip("origin/main 을 참조할 수 없는 환경")
    assert diff.stdout.strip() == "", f"C1 위반:\n{diff.stdout}"


def test_eligibility_thresholds_are_unchanged() -> None:
    """C4 — **D2 는 목록이 잘못된 것이지 기준이 아니다.**"""
    assert fe.FOLLOW_MIN_SAMPLE == 30
    assert fe.FOLLOW_MIN_WIN_PCT == 55.0
    assert fe.FOLLOW_EXCLUDED_TYPES == frozenset({participant_type.TYPE_MARKET_MAKER})


def test_mdd_threshold_is_unchanged() -> None:
    """C5 — 19.41% 가 20% 에 근접했다고 목표를 올리지 않는다.

    > **전제 정정**: WO 는 `축 7 서명값 20%` 를 말하지만 그런 코드 임계는 없다. 전환 게이트
    > (`live_trading_gate`)는 6축이고 MDD 축이 없으며, 존재하는 유일한 MDD 설정
    > `FCE_PERFORMANCE_MONTHLY_MDD_LIMIT_PCT` 는 기본값 **0.0(비활성)** 이다.
    >
    > 그래서 이 회귀는 "임계를 올리지 않았다"를 **diff 로** 고정한다 — 없는 상수를 가짜로
    > 만들어 pin 하면 그것이 새 임계가 되고, 그것이야말로 C5 위반이다.
    """
    from app.core.config import Settings

    assert Settings().performance_monthly_mdd_limit_pct == 0.0

    diff = subprocess.run(
        ["git", "diff", "origin/main", "--", "backend/app/core/config.py", "backend/app/validation/live_trading_gate.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if diff.returncode != 0:
        pytest.skip("origin/main 을 참조할 수 없는 환경")
    changed = [line for line in diff.stdout.splitlines() if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
    assert not any("mdd" in line.lower() for line in changed), f"MDD 임계가 변경됐다:\n{changed}"


def test_no_liquidation_path_was_added() -> None:
    """C6 — 정지 트랙 포지션을 임의 청산하지 않는다."""
    for path in ("backend/app/notify/daily_report.py", "backend/app/notify/daily_report_source.py", "backend/app/onchain/follow_report.py"):
        source = (REPO_ROOT / path).read_text(encoding="utf-8").lower()
        for forbidden in ("close_position", "liquidate", "force_exit", "upsert_"):
            assert forbidden not in source, f"{path} 에 원장 변경 경로가 있다: {forbidden}"
