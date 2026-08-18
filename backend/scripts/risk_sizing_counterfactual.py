"""WO-FCE-RISK-SIZING-01 Phase 4 — 반사실 재산출 CLI.

Phase 1·2·3 의 숫자를 산출한 스크립트가 커밋되지 않아 Phase 3-5 의 합계를 재현할 수
없었다. 이 스크립트가 그 경로를 정본으로 고정한다. **읽기 전용이며 DB 에 쓰지 않는다.**

실행:
    cd backend && PYTHONPATH=. python3 scripts/risk_sizing_counterfactual.py
    ... --database <path>      # 기본값은 settings.database_url 의 sqlite 경로
"""

from __future__ import annotations

import argparse
import sys

from app.core.config import get_settings
from app.db.maintenance import sqlite_path
from app.validation.risk_sizing_replay import (
    Metrics,
    DEFAULT_RISK_BUDGET_USDT,
    GAP_EXCESS_R_THRESHOLD,
    PortfolioCaps,
    apply_portfolio_caps,
    gap_budget,
    gap_distribution,
    load_trades,
    metrics,
    same_bar_reentry_blocked,
    stop_executions,
)


# Phase 3-5 가 발표한 값. 재현 여부를 **매 실행마다 대조**한다 — 어긋나면 크게 적는다.
PHASE35_CURRENT = {"n": 33, "gross": 1.037, "cost": 4.072, "net": -3.035, "pf": 0.8204, "mdd": 23.94}
PHASE35_LOCKED = {"n": 24, "gross": 1.950, "cost": 2.945, "net": -0.995, "pf": 0.9109, "mdd": 13.95}

# 4-3 상한 3안. 상관 군집은 **추정**이다 — §"상관" 참조.
#
# BTC·ETH 는 시장 대표(major), 그 외 USDT 무기한은 사실상 BTC 베타 하나로 움직인다는
# 가정으로 한 군집에 넣는다. 이 분류는 측정이 아니라 **선언**이며 근거는 문서에 남긴다.
CLUSTER_OF = {
    "BTCUSDT": "major",
    "ETHUSDT": "major",
    "HYPEUSDT": "alt_beta",
    "BASEDUSDT": "alt_beta",
    "SPCXUSDT": "alt_beta",
}

CAP_OPTIONS = (
    PortfolioCaps(label="현행 (상한 없음)"),
    PortfolioCaps(label="느슨", max_total_risk_usdt=10.0, max_same_direction_positions=4, max_cluster_positions=3, cluster_of=CLUSTER_OF),
    PortfolioCaps(label="중간", max_total_risk_usdt=7.5, max_same_direction_positions=3, max_cluster_positions=2, cluster_of=CLUSTER_OF),
    PortfolioCaps(label="조임", max_total_risk_usdt=5.0, max_same_direction_positions=2, max_cluster_positions=1, cluster_of=CLUSTER_OF),
)

# 3안은 세 축을 **동시에** 움직이므로 무엇이 효과였는지 알 수 없다(AGENTS.md "한 번에 하나씩").
# 축을 하나씩 격리한 행을 함께 낸다 — 3안 표만으로는 교란된 결과다.
CAP_AXES = (
    PortfolioCaps(label="총리스크 10 만", max_total_risk_usdt=10.0),
    PortfolioCaps(label="총리스크 7.5 만", max_total_risk_usdt=7.5),
    PortfolioCaps(label="총리스크 5 만", max_total_risk_usdt=5.0),
    PortfolioCaps(label="동일방향 4 만", max_same_direction_positions=4),
    PortfolioCaps(label="동일방향 3 만", max_same_direction_positions=3),
    PortfolioCaps(label="동일방향 2 만", max_same_direction_positions=2),
    PortfolioCaps(label="군집 3 만", max_cluster_positions=3, cluster_of=CLUSTER_OF),
    PortfolioCaps(label="군집 2 만", max_cluster_positions=2, cluster_of=CLUSTER_OF),
    PortfolioCaps(label="군집 1 만", max_cluster_positions=1, cluster_of=CLUSTER_OF),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="RISK-SIZING-01 반사실 재산출 (읽기 전용)")
    parser.add_argument("--database", default=None, help="sqlite 경로 (기본: settings.database_url)")
    args = parser.parse_args()

    database = args.database
    if database is None:
        resolved = sqlite_path(get_settings().database_url)
        if resolved is None:
            print("sqlite 데이터베이스가 아니다 — --database 로 경로를 지정하라.", file=sys.stderr)
            return 2
        database = str(resolved)

    trades = load_trades(database)
    if not trades:
        print(f"계수 대상 거래가 없다: {database}", file=sys.stderr)
        return 1

    blocked_ids = same_bar_reentry_blocked(trades)
    locked = [item for item in trades if item.trade_id not in blocked_ids]
    blocked = [item for item in trades if item.trade_id in blocked_ids]

    print("=" * 118)
    print(f"WO-FCE-RISK-SIZING-01 반사실 재산출 — {database}")
    print(f"리스크 예산 {DEFAULT_RISK_BUDGET_USDT} USDT · R 은 사이즈 불변이므로 원장 수량에서 직접 산출")
    print("=" * 118)

    current_metrics = metrics(trades)
    locked_metrics = metrics(locked)
    blocked_metrics = metrics(blocked)

    print("\n## 1. 기준선 (Phase 3 잠금 적용 전/후)\n")
    print(current_metrics.as_row("현행 (잠금 없음)"))
    print(blocked_metrics.as_row("  └ same_bar 차단분"))
    print(locked_metrics.as_row("same_bar 잠금 적용"))

    print("\n## 2. Phase 3-5 발표값 대조\n")
    _compare("현행", current_metrics, PHASE35_CURRENT)
    _compare("잠금", locked_metrics, PHASE35_LOCKED)

    print("\n## 3. 비용률 민감도 (잠금 적용 모집단)\n")
    print(f"{'체결 전제':<28}{'편도%':>7}{'왕복%':>7}{'비용R':>8}{'netR':>9}{'우위/비용':>10}{'금액':>9}{'PF':>9}{'MDD':>8}")
    for label, one_way in (
        ("현행 (테이커+슬립 0.03)", 0.090),
        ("테이커만 (슬립 0)", 0.060),
        ("메이커 + 슬립 0.03", 0.050),
        ("메이커 + 슬립 0 (지정가)", 0.020),
    ):
        row = metrics(locked, one_way_cost_pct=one_way)
        ratio = "n/a" if row.edge_to_cost_ratio is None else f"{row.edge_to_cost_ratio:.2f}배"
        pf = "n/a" if row.profit_factor is None else f"{row.profit_factor:.4f}"
        print(f"{label:<28}{one_way:7.3f}{one_way * 2:7.3f}{row.cost_r:8.3f}{row.net_r:+9.3f}{ratio:>10}{row.net_pnl_usdt:+9.2f}{pf:>9}{row.mdd_usdt:8.2f}")
    print("  ⚠️ 지정가 행은 미체결을 모델링하지 않았다 — 그대로 채택하면 낙관 편향이다.")

    print("\n## 4. 갭 예산 (4-2) — 손절 체결 실효 리스크\n")
    stops = stop_executions(trades)
    print(f"{'심볼':<12}{'방향':<7}{'무효화가':>14}{'체결가':>14}{'이탈%':>9}{'초과R':>8}{'실효R':>8}{'실효금액':>10}  판정")
    for row in stops:
        verdict = "갭" if row.is_gap else "종가규칙"
        print(
            f"{row.trade.symbol:<12}{row.trade.direction:<7}{row.trade.invalidation_price:>14.6g}"
            f"{row.trade.exit_price or 0:>14.6g}{row.overshoot_pct:>9.3f}{row.excess_r:>+8.3f}"
            f"{row.effective_risk_r:>8.3f}{row.effective_risk_usdt:>10.2f}  {verdict}"
        )
    budget = gap_budget(stops)
    if budget is not None:
        print(
            f"\n  전량 기준 N={budget.sample_size}: 계획 {budget.planned_risk_usdt:.2f} → "
            f"실효 {budget.mean_effective_risk_usdt:.2f} USDT ({budget.multiple:.3f}배)"
        )
        print(f"  체결 이탈만 (gross 기준): 실효 {budget.mean_execution_risk_r:.3f}R — 나머지는 마찰 비용 소관이다")
        print(f"  갭 {budget.gap_count}건 평균 초과 {budget.gap_mean_excess_r:+.3f}R — 전체 초과분의 {budget.gap_share_of_excess_pct:.1f}%")
        print(f"  종가규칙 {budget.close_rule_count}건 평균 초과 {budget.close_rule_mean_excess_r:+.3f}R")
        print(f"  갭 판정 임계: 초과R > {GAP_EXCESS_R_THRESHOLD:g} (= 한 봉이 스톱 거리 전체보다 멀리 이탈)")
        print(f"  ⚠️ N={budget.sample_size} 은 표본 부족이다. 이 배수를 예산에 곱해 확정하지 않는다.")

    print("\n  빈도·크기 분포\n")
    distribution = gap_distribution(stops)
    for axis, label in (("by_symbol", "심볼별"), ("by_entry_hour_utc", "진입 시간대별 (UTC)")):
        print(f"  [{label}]")
        print(f"    {'구분':<12}{'손절':>6}{'갭':>5}{'평균초과R':>11}{'최대초과R':>11}{'최대이탈%':>11}")
        for row in distribution[axis]:
            print(
                f"    {row['key']:<12}{row['stops']:>6}{row['gaps']:>5}{row['mean_excess_r']:>+11.3f}"
                f"{row['max_excess_r']:>+11.3f}{row['max_overshoot_pct']:>11.3f}"
            )

    print("\n## 5. 포트폴리오 상한 (4-3) — 잠금 적용 모집단 위에서\n")
    print("  [축 격리 — 한 축만 걸었을 때]")
    print(f"  {'상한':<18}{'N':>5}{'보류':>6}{'netR':>9}{'PF':>9}{'MDD':>8}")
    baseline = metrics(locked)
    for caps in CAP_AXES:
        replay = apply_portfolio_caps(locked, caps)
        row = metrics(replay.kept)
        pf = "n/a" if row.profit_factor is None else f"{row.profit_factor:.4f}"
        print(f"  {caps.label:<18}{row.sample_size:>5}{len(replay.blocked):>6}{row.net_r:+9.3f}{pf:>9}{row.mdd_usdt:8.2f}")
    print(f"  (기준선 N={baseline.sample_size} netR={baseline.net_r:+.3f} MDD={baseline.mdd_usdt:.2f})")

    print("\n  [3안 — 세 축 동시. 축 격리 표 없이는 무엇이 효과인지 알 수 없다]")
    print(f"  {'안':<16}{'총리스크':>9}{'동일방향':>9}{'군집':>6}{'N':>5}{'보류':>6}{'netR':>9}{'PF':>9}{'MDD':>8}")
    for caps in CAP_OPTIONS:
        replay = apply_portfolio_caps(locked, caps)
        row = metrics(replay.kept)
        pf = "n/a" if row.profit_factor is None else f"{row.profit_factor:.4f}"
        total = "-" if caps.max_total_risk_usdt is None else f"{caps.max_total_risk_usdt:g}"
        direction = "-" if caps.max_same_direction_positions is None else str(caps.max_same_direction_positions)
        cluster = "-" if caps.max_cluster_positions is None else str(caps.max_cluster_positions)
        print(f"  {caps.label:<16}{total:>9}{direction:>9}{cluster:>6}{row.sample_size:>5}{len(replay.blocked):>6}{row.net_r:+9.3f}{pf:>9}{row.mdd_usdt:8.2f}")

    print("\n  보류된 진입 (사유 포함 — C11)\n")
    for caps in CAP_OPTIONS[1:]:
        replay = apply_portfolio_caps(locked, caps)
        print(f"  [{caps.label}] {len(replay.blocked)}건")
        for trade, reason in replay.blocked:
            print(f"    {trade.symbol:<11}{trade.entry_bar_at.isoformat()}  {trade.direction:<6}netR={trade.net_r:+7.3f}  {reason}")

    print("\n" + "=" * 118)
    return 0


def _compare(label: str, actual: Metrics, published: dict[str, float]) -> None:
    pairs: tuple[tuple[str, float, float, int], ...] = (
        ("N", float(actual.sample_size), published["n"], 0),
        ("gross", actual.gross_r, published["gross"], 3),
        ("비용", actual.cost_r, published["cost"], 3),
        ("net", actual.net_r, published["net"], 3),
        ("PF", actual.profit_factor or 0.0, published["pf"], 4),
        ("MDD", actual.mdd_usdt, published["mdd"], 2),
    )
    cells = []
    mismatched = False
    for name, value, expected, digits in pairs:
        ok = abs(value - expected) <= (0.5 if digits == 0 else 10**-digits * 5)
        mismatched = mismatched or not ok
        cells.append(f"{name}={value:.{digits}f}/{expected:.{digits}f}{'' if ok else ' ✗'}")
    verdict = "재현 실패" if mismatched else "재현"
    print(f"  {label:<6}{verdict:<10}" + "  ".join(cells))


if __name__ == "__main__":
    raise SystemExit(main())
