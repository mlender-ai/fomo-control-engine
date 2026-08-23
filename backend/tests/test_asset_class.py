"""WO-FCE-ASSET-CLASS-01 3-2·3-3 — 자산군 오분류 감사 회귀.

고정하는 명제:

1. **같은 함수가 입력에 따라 갈린다** — 버그는 `classify_asset_class` 가 아니라 호출부다
2. **`earnings_clear` 는 stock·index 에서 영구 불통과** — 실적 공급원이 없다(D3 정정)
3. 분류만 고치면 그 심볼들의 진입이 **0이 된다** — 감사가 그것을 판정으로 낸다
4. **감사는 분류를 바꾸지 않는다** (C1 — 3-1 종결 전 3-2 착수 금지)
5. 게이트 임계 diff 0줄 (C2) · `analyst/`·`structure/` diff 0줄 (C3)
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.db.models import CatalogSymbol
from app.marketdata import asset_class_audit as aca
from app.marketdata.assets import STOCK_TICKERS, classify_asset_class
from app.paper.service import _earnings_clear

REPO_ROOT = Path(__file__).resolve().parents[2]


def _catalog(*specs: tuple[str, str, bool]) -> list[CatalogSymbol]:
    return [
        CatalogSymbol(
            symbol=f"{ticker}USDT",
            base_coin=ticker,
            quote_coin="USDT",
            asset_class=asset_class,
            raw_metadata={"isRwa": "YES" if is_rwa else "NO"},
        )
        for ticker, asset_class, is_rwa in specs
    ]


# ── 1. 버그는 함수가 아니라 호출부다 (D2) ───────────────────────────────


def test_same_function_splits_on_input_not_on_symbol() -> None:
    """`AAPLUSDT → stock` · `INTCUSDT → crypto`. 같은 상품이 이름으로 갈린다."""
    assert classify_asset_class("AAPLUSDT") == "stock", "허용목록에 있어 이름만으로도 맞는다"
    assert classify_asset_class("INTCUSDT") == "crypto", "허용목록에 없어 이름만으로는 틀린다"

    # 메타데이터를 주면 둘 다 stock 이다 — 함수는 정상이고 입력이 빠져 있었다.
    for symbol in ("AAPLUSDT", "INTCUSDT"):
        assert classify_asset_class(symbol, symbol.removesuffix("USDT"), "USDT", {"isRwa": "YES"}) == "stock"


def test_allowlist_cannot_cover_the_rwa_universe() -> None:
    """27개 허용목록으로 294개 RWA 를 덮을 수 없다 — 구조적 미달이다."""
    assert len(STOCK_TICKERS) < 100, "허용목록이 RWA 규모에 못 미친다는 전제가 이 WO 의 출발점이다"


def test_diff_reports_both_paths_without_reclassifying() -> None:
    diffs = aca.classification_diffs(_catalog(("INTC", "stock", True), ("BTC", "crypto", False)))

    by_symbol = {row.symbol: row for row in diffs}
    assert by_symbol["INTCUSDT"].catalog_class == "stock"
    assert by_symbol["INTCUSDT"].name_based_class == "crypto"
    assert by_symbol["INTCUSDT"].changed is True
    assert by_symbol["BTCUSDT"].changed is False


def test_summary_counts_transitions_and_lists_symbols() -> None:
    diffs = aca.classification_diffs(_catalog(("INTC", "stock", True), ("DELL", "stock", True), ("QQQ", "index", True), ("BTC", "crypto", False)))

    summary = aca.misclassification_summary(diffs)

    assert summary["total_symbols"] == 4
    assert summary["rwa_symbols"] == 3
    # QQQ 는 INDEX_TICKERS 에 있어 이름만으로도 index 다 — 갈리는 것은 INTC·DELL 둘.
    assert summary["changed"] == 2
    assert summary["transitions"] == {"crypto→stock": 2}
    assert {row["symbol"] for row in summary["symbols"]} == {"INTCUSDT", "DELLUSDT"}


def test_changed_symbols_record_the_gates_they_gain() -> None:
    diffs = aca.classification_diffs(_catalog(("INTC", "stock", True)))

    row = aca.misclassification_summary(diffs)["symbols"][0]

    assert row["gates_gained"] == ["stage2_template", "earnings_clear"]


# ── 2. `earnings_clear` 는 영구 차단이다 (D3 정정) ──────────────────────


def test_earnings_gate_is_permanently_closed_for_stock_and_index() -> None:
    """**D3 의 전제를 정정한다.**

    WO 는 "오분류된 262종이 `earnings_window` 를 건너뛴다"고 봤다. 맞다. 그러나 분류를
    고치면 게이트가 "제대로 걸리는" 것이 아니라 **영구 차단**이 된다 —
    `analysis['earnings']` 를 채우는 코드가 크립토 분석 경로에 없기 때문이다.
    """
    audit = aca.audit_earnings_gate_inputs(_earnings_clear)

    assert audit["by_asset_class"]["crypto"]["clear_without_earnings_data"] is True
    assert audit["by_asset_class"]["stock"]["clear_without_earnings_data"] is False
    assert audit["by_asset_class"]["index"]["clear_without_earnings_data"] is False
    # 데이터가 있으면 통과한다 — 게이트 로직이 아니라 **공급원**이 없는 것이다.
    assert audit["by_asset_class"]["stock"]["clear_with_earnings_data"] is True
    assert audit["permanently_blocked_classes"] == ["index", "stock"]
    assert audit["feed_present"] is False


def test_impact_names_the_zero_entry_consequence() -> None:
    """표본을 늘리려는 변경이 표본을 0으로 만드는 형태를 **판정으로** 낸다."""
    diffs = aca.classification_diffs(_catalog(("INTC", "stock", True), ("DELL", "stock", True)))
    audit = aca.audit_earnings_gate_inputs(_earnings_clear)

    impact = aca.reclassification_impact(diffs, earnings_audit=audit)

    assert impact["symbols_gaining_gates"] == 2
    assert impact["symbols_blocked_by_missing_earnings_feed"] == 2
    assert impact["earnings_feed_present"] is False
    assert "0이 된다" in impact["verdict"]


def test_impact_is_clean_when_a_feed_exists() -> None:
    """공급원이 생기면 판정이 바뀐다 — 감사가 결론을 고정하고 있지 않다는 확인."""
    diffs = aca.classification_diffs(_catalog(("INTC", "stock", True)))

    impact = aca.reclassification_impact(diffs, earnings_audit={"permanently_blocked_classes": [], "feed_present": True})

    assert impact["symbols_blocked_by_missing_earnings_feed"] == 0
    assert "단계 확대" in impact["verdict"]


def test_no_change_means_no_verdict_noise() -> None:
    diffs = aca.classification_diffs(_catalog(("BTC", "crypto", False)))

    impact = aca.reclassification_impact(diffs, earnings_audit=aca.audit_earnings_gate_inputs(_earnings_clear))

    assert impact["symbols_gaining_gates"] == 0
    assert "대상 없음" in impact["verdict"]


# ── 3. 세션 필터 (3-4) ──────────────────────────────────────────────────


def test_required_supply_accounts_for_the_session_filter() -> None:
    """분류를 고치면 262종이 세션 필터를 새로 받는다 — 200봉 요건이 더 멀어진다."""
    diffs = aca.classification_diffs(_catalog(("INTC", "stock", True)))

    impact = aca.reclassification_impact(diffs, earnings_audit=aca.audit_earnings_gate_inputs(_earnings_clear))

    # 30% 손실이면 200봉을 남기기 위해 286봉이 필요하다.
    assert impact["required_supply_for_stage2"] == 286
    assert impact["session_filter_loss_pct"] == pytest.approx(30.0)


# ── 4. 감사는 아무것도 바꾸지 않는다 (C1·C2·C3) ─────────────────────────


def test_audit_module_does_not_mutate_classification() -> None:
    source = (Path(__file__).resolve().parents[1] / "app" / "marketdata" / "asset_class_audit.py").read_text(encoding="utf-8")

    # 감사는 **호출**만 한다. 분류를 새로 정의하거나 허용목록을 고치면 그 순간 계측이 아니다.
    assert "STOCK_TICKERS =" not in source, "허용목록을 재정의하고 있다"
    assert "def classify_asset_class" not in source, "분류를 재구현하고 있다"


@pytest.mark.parametrize(
    "path",
    [
        "backend/app/marketdata/assets.py",
        "backend/app/scout/universe.py",
        "backend/app/paper/policy.py",
        "backend/app/analyst/",
        "backend/app/structure/",
    ],
)
def test_constrained_paths_have_zero_diff(path: str) -> None:
    """C1·C2·C3 — 분류·게이트 임계·판정 로직은 아직 한 줄도 바뀌지 않았다."""
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


def test_asset_class_document_records_the_earnings_blocker() -> None:
    doc = (REPO_ROOT / "docs" / "validation" / "ASSET_CLASS.md").read_text(encoding="utf-8")

    assert "earnings_clear" in doc
    assert "영구" in doc, "실적 게이트가 영구 차단이라는 사실이 정본에 없다"
    assert "3-1" in doc, "C1 선행 조건이 정본에 없다"
