"""WO-FCE-REPLAY-DEPTH-01 4-4 — 페이퍼 재판정 하네스 회귀.

고정하는 명제:

1. **룩어헤드 부재**(C6) — 입력 구간을 잘라도 그 구간의 거래가 전체 입력 시와 동일하다
2. **발표값 자동 대조** — 커밋된 픽스처 위의 기준선이 CI 에서 강제된다
3. **손절 체결 반사실** — 봉 중간 터치 vs 종가가 같은 캔들·같은 정책 위에서 산출된다
4. **제약 diff 0줄** — `paper/policy.py` · `analyst/` · `structure/` 가 변경되지 않았다
5. **파라미터 스윕**이 실행 가능하고 축을 하나씩 가른 행을 낼 수 있다
6. 산출물이 **재판정임을 명시**한다(C9)
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from app.db.models import MarketCandle
from app.paper.policy import PaperPolicy
from app.validation import paper_replay as pr
from app.validation import published_values as pv

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "replay_candles_4h.json"

# 픽스처 정책. `record_only` · `stable_direction` 은 현재 `params/crypto-v2.json` 이 켜는
# 조합이며, 기본값(`required` · `confirmed_flip`)으로는 재판정 진입이 0건이라 회귀망이
# 아무것도 잡지 못한다.
FIXTURE_POLICY = PaperPolicy(stance_gate_mode="stable_direction", signature_gate_mode="record_only")

# 절단점. 분석 창(100봉)보다 충분히 커야 겹치는 판정 지점이 생긴다.
TRUNCATION = 150


def _fixture_candles() -> list[MarketCandle]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [
        MarketCandle(
            timestamp=datetime.fromisoformat(row["timestamp"]),
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=row["volume"],
        )
        for row in payload["candles"]
    ]


@pytest.fixture(scope="module")
def candles() -> list[MarketCandle]:
    return _fixture_candles()


@pytest.fixture(scope="module")
def close_result(candles: list[MarketCandle]) -> pr.ReplayResult:
    return pr.replay_paper_engine(symbol="REPLAYUSDT", timeframe="4h", candles=candles, policy=FIXTURE_POLICY)


@pytest.fixture(scope="module")
def intrabar_result(candles: list[MarketCandle]) -> pr.ReplayResult:
    return pr.replay_paper_engine(symbol="REPLAYUSDT", timeframe="4h", candles=candles, policy=FIXTURE_POLICY, stop_fill="intrabar")


@pytest.fixture(scope="module")
def truncated_result(candles: list[MarketCandle]) -> pr.ReplayResult:
    return pr.replay_paper_engine(symbol="REPLAYUSDT", timeframe="4h", candles=candles[:TRUNCATION], policy=FIXTURE_POLICY)


# ── 1. 룩어헤드 부재 (C6) ───────────────────────────────────────────────


def test_prefix_invariance_proves_no_lookahead(close_result: pr.ReplayResult, truncated_result: pr.ReplayResult) -> None:
    """구간을 잘라도 그 구간에서 **완결된** 거래는 전체 입력 시와 완전히 동일해야 한다.

    절단점 이후에 청산된 거래는 잘린 입력에서 아직 열려 있으므로 비교 대상이 아니다 —
    그것을 비교하면 "미래를 못 봤다"가 아니라 "미래가 아직 안 왔다"를 재게 된다.
    """
    cutoff = truncated_result.trades[-1].entry_bar_at if truncated_result.trades else None
    assert cutoff is not None, "절단 입력에서도 거래가 나와야 이 테스트가 무언가를 증명한다"

    full_closed = {trade.entry_bar_at.isoformat(): trade for trade in close_result.trades if trade.status == "closed" and trade.exit_bar_at is not None}
    compared = 0
    for trade in truncated_result.trades:
        if trade.status != "closed":
            continue
        key = trade.entry_bar_at.isoformat()
        assert key in full_closed, f"{key} 진입이 전체 입력에서 사라졌다 — 뒤쪽 봉이 앞쪽 판정을 바꿨다"
        reference = full_closed[key]
        assert pr.trades_digest([trade]) == pr.trades_digest([reference]), f"{key} 거래가 입력 길이에 따라 달라졌다 — 룩어헤드"
        compared += 1
    assert compared > 0, "겹치는 완결 거래가 없으면 이 테스트는 아무것도 증명하지 않는다"


def test_replay_is_deterministic(candles: list[MarketCandle], close_result: pr.ReplayResult) -> None:
    again = pr.replay_paper_engine(symbol="REPLAYUSDT", timeframe="4h", candles=candles, policy=FIXTURE_POLICY)

    assert pr.trades_digest(again.trades) == pr.trades_digest(close_result.trades)


def test_open_trade_at_the_edge_is_not_force_closed(close_result: pr.ReplayResult) -> None:
    """구간 끝에 열려 있는 건을 청산으로 적으면 표본이 실제보다 좋거나 나빠진다."""
    metrics = pr.replay_metrics(close_result)

    assert metrics["trades_closed"] == len([trade for trade in close_result.trades if trade.status == "closed"])
    assert metrics["trades_closed"] <= metrics["trades_opened"]


# ── 2. 발표값 자동 대조 (4-4 작업 4) ────────────────────────────────────


def test_fixture_baseline_matches_published_values(close_result: pr.ReplayResult) -> None:
    """**Phase 3-5 오프셋 사고의 재발 방지책이다.** 어긋나면 CI 가 실패한다."""
    result = pv.compare("replay_fixture.close", pr.replay_metrics(close_result))

    assert result["failures"] == [], f"재판정 기준선이 발표값과 어긋났다: {result['failures']}"


def test_intrabar_baseline_matches_published_values(intrabar_result: pr.ReplayResult) -> None:
    result = pv.compare("replay_fixture.intrabar", pr.replay_metrics(intrabar_result))

    assert result["failures"] == [], f"봉 중간 터치 기준선이 발표값과 어긋났다: {result['failures']}"


def test_unregistered_published_value_is_reported_not_ignored() -> None:
    result = pv.compare("does.not.exist", {"net_r": 0.0})

    assert result["status"] == "unregistered"
    assert result["failures"]


def test_non_reproducing_entries_pin_their_known_offset() -> None:
    """재현 안 되는 값을 지우지 않고 **오프셋을 고정한다.** 오프셋이 변하면 새 드리프트다."""
    entry = pv.PUBLISHED_VALUES["risk_sizing.phase3_5.current"]
    assert entry.reproduces is False
    assert entry.known_offset["net_r"] == pytest.approx(-1.902)

    matching = {name: value + entry.known_offset.get(name, 0.0) for name, value in entry.values.items()}
    assert pv.compare(entry.key, matching)["failures"] == []

    drifted = {**matching, "net_r": matching["net_r"] + 0.5}
    assert pv.compare(entry.key, drifted)["failures"]


def test_database_backed_entries_are_skipped_not_silently_passed() -> None:
    """CI 에는 DB 가 없다. 건너뛴 항목이 **목록에 남아야** 대조 여부가 구분된다."""
    summary = pv.compare_all({})

    skipped = {item["key"] for item in summary["skipped"]}
    assert "risk_sizing.phase3_5.current" in skipped
    assert "stop_execution.phase2" in skipped
    assert all(item["reason"] in {"database_required", "not_measured"} for item in summary["skipped"])


def test_every_registry_entry_names_its_source_document() -> None:
    for key, entry in pv.PUBLISHED_VALUES.items():
        assert entry.source_doc.startswith("docs/"), f"{key} 가 정본 문서를 가리키지 않는다"
        assert entry.values, f"{key} 에 대조할 값이 없다"
        if not entry.reproduces:
            assert entry.known_offset, f"{key} 는 재현되지 않는데 알려진 오프셋이 없다"


# ── 3. 손절 체결 반사실 (Phase 2 이월 종결) ──────────────────────────────


def test_intrabar_stop_fills_at_the_stop_price_not_the_close(intrabar_result: pr.ReplayResult) -> None:
    stops = [trade for trade in intrabar_result.trades if trade.exit_reason in {"invalidation_breach", "breakeven_stop"}]
    assert stops, "손절 건이 없으면 이 반사실은 아무것도 재지 않는다"
    for trade in stops:
        assert trade.exit_price == pytest.approx(trade.stop_price), "봉 중간 터치 체결가는 무효화가여야 한다"


def test_close_mode_fills_at_the_bar_close(close_result: pr.ReplayResult, candles: list[MarketCandle]) -> None:
    """라이브 현행 규칙 그대로다 — 손절은 종가에 체결된다(`policy._stop_breached`).

    비대칭의 실물이 여기 있다. 익절은 봉 중간 터치 가격에 체결되는데 손절만 봉이 닫히기를
    기다린다 — 4시간봉에서 종가는 무효화선에서 멀리 가 있다(`STOP_EXECUTION.md` §0).
    """
    close_by_time = {candle.timestamp: candle.close for candle in candles}
    checked = 0
    for trade in close_result.trades:
        if trade.exit_reason != "invalidation_breach" or trade.exit_price is None or trade.exit_bar_at is None:
            continue
        assert trade.exit_price == pytest.approx(close_by_time[trade.exit_bar_at]), "종가 모드가 봉 중간에서 체결됐다"
        checked += 1
    assert checked > 0, "손절 건이 없으면 이 규칙을 확인할 수 없다"


def test_stop_execution_counterfactual_separates_the_two_rules(close_result: pr.ReplayResult, intrabar_result: pr.ReplayResult) -> None:
    close_metrics = pr.replay_metrics(close_result)
    intrabar_metrics = pr.replay_metrics(intrabar_result)

    assert close_metrics["stop_fill"] == "close"
    assert intrabar_metrics["stop_fill"] == "intrabar"
    # 같은 캔들·같은 정책이므로 차이는 전부 체결 규칙에 귀속된다.
    assert close_metrics["judgment_points"] == intrabar_metrics["judgment_points"]
    assert close_metrics["net_r"] != intrabar_metrics["net_r"]


# ── 4. 제약 diff 0줄 증명 ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "backend/app/paper/policy.py",
        "backend/app/analyst/",
        "backend/app/structure/",
    ],
)
def test_constrained_paths_have_zero_diff(path: str) -> None:
    """C1·C2·C3·C4 — 진입 게이트·판정·방향 로직·게이트 임계는 한 줄도 바뀌지 않았다."""
    diff = subprocess.run(
        ["git", "diff", "origin/main", "--stat", "--", path],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if diff.returncode != 0:
        pytest.skip("origin/main 을 참조할 수 없는 환경")

    assert diff.stdout.strip() == "", f"제약 경로가 변경됐다:\n{diff.stdout}"


def test_harness_does_not_reimplement_the_gates() -> None:
    source = (Path(__file__).resolve().parents[1] / "app" / "validation" / "paper_replay.py").read_text(encoding="utf-8")

    # 게이트를 **호출**해야 한다. 여기서 조건을 다시 쓰면 재판정이 라이브를 대변하지 못한다.
    for symbol in ("evaluate_entry", "evaluate_exit", "open_trade", "apply_exit_decision", "reentry_locked"):
        assert symbol in source, f"{symbol} 를 호출하지 않는다 — 게이트를 재구현했을 가능성"
    assert "min_evidence" not in source, "게이트 임계를 하네스가 다시 정의하고 있다"
    assert "min_checklist_passed" not in source


# ── 5. 파라미터 스윕 (4-4 작업 5) ────────────────────────────────────────


def test_sweep_runs_one_axis_at_a_time(candles: list[MarketCandle]) -> None:
    """여러 축을 동시에 움직인 행만 내면 무엇이 효과였는지 영원히 알 수 없다."""
    variants = pr.policy_variants(
        FIXTURE_POLICY,
        [
            ("현행", {}),
            ("잠금 same_bar", {"reentry_lock_mode": "same_bar"}),
        ],
    )
    # 스윕 자체는 재판정 2회다. 짧은 구간으로 실행 가능성만 고정한다.
    result = pr.sweep(symbol="REPLAYUSDT", timeframe="4h", candles=candles[:130], variants=variants)

    assert [row["policy"] for row in result["rows"]] == ["현행", "잠금 same_bar"]
    assert result["overfit_warning"]
    assert all(row["kind"] == pr.REPLAY_KIND for row in result["rows"])


def test_policy_variants_do_not_mutate_the_base_policy() -> None:
    variants = pr.policy_variants(FIXTURE_POLICY, [("잠금", {"reentry_lock_mode": "same_bar"})])

    assert FIXTURE_POLICY.reentry_lock_mode == "off"
    assert variants[0][1].reentry_lock_mode == "same_bar"


# ── 6. 정직성 (C9) ──────────────────────────────────────────────────────


def test_every_payload_states_that_it_is_a_counterfactual(close_result: pr.ReplayResult) -> None:
    metrics = pr.replay_metrics(close_result)

    assert metrics["kind"] == "replay_counterfactual"
    assert "라이브 실적이 아니다" in metrics["disclaimer"]


def test_unreproducible_inputs_are_named_not_hidden(close_result: pr.ReplayResult) -> None:
    """저장되지 않은 입력을 조용히 통과시키면 재판정이 라이브보다 관대해진다."""
    assumptions = pr.replay_metrics(close_result)["assumptions"]

    assert assumptions["signature_gate"] is True
    assert assumptions["unavailable_checklist_items"] == ["funding", "volume"]
    assert assumptions["derivative_history"] == "not_included"


def test_assumed_checklist_items_are_recorded_per_trade(close_result: pr.ReplayResult) -> None:
    assert close_result.trades, "거래가 없으면 이 기록을 확인할 수 없다"
    for trade in close_result.trades:
        checklist = trade.checklist or {}
        assert "assumed_items" in checklist
        assert checklist["evaluated_total"] <= checklist["total"]


def test_blocking_policy_makes_the_structural_gap_visible(candles: list[MarketCandle]) -> None:
    """`block` 을 고르면 재판정 진입이 **구조적으로 0건**이 된다 — 그것이 저장 공백의 크기다."""
    strict = pr.ReplayAssumptions(unavailable_checklist_policy="block")
    result = pr.replay_paper_engine(
        symbol="REPLAYUSDT",
        timeframe="4h",
        candles=candles[:130],
        policy=FIXTURE_POLICY,
        assumptions=strict,
    )

    assert result.trades == []
    assert result.entry_blocks.get("gate:checklist", 0) > 0


def test_invalid_stop_fill_mode_is_rejected(candles: list[MarketCandle]) -> None:
    with pytest.raises(ValueError):
        pr.replay_paper_engine(symbol="REPLAYUSDT", timeframe="4h", candles=candles[:110], policy=FIXTURE_POLICY, stop_fill="magic")


def test_replay_harness_document_records_the_second_layer() -> None:
    doc = (REPO_ROOT / "docs" / "validation" / "REPLAY_HARNESS.md").read_text(encoding="utf-8")

    assert "페이퍼 엔진 재판정" in doc
    assert "stop_fill" in doc, "손절 체결 반사실이 문서에 없다"
    assert "published_values" in doc, "발표값 자동 대조가 문서에 없다"
    # CI 기준선 표가 문서와 레지스트리에서 갈라지면 "발표값 대조"가 스스로를 못 지킨다.
    for value in ("10.3398", "8.8951"):
        assert value in doc, f"CI 기준선 {value} 가 문서에 없다"


def test_stop_execution_document_closes_the_phase2_carryover() -> None:
    doc = (REPO_ROOT / "docs" / "validation" / "STOP_EXECUTION.md").read_text(encoding="utf-8")

    assert "paper_replay_report.py" in doc, "반사실 실행 수단이 문서에 없다"
    assert "diff **0줄**" in doc, "정책 무변경 증명이 문서에 없다"
