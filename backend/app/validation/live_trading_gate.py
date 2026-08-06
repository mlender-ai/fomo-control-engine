"""WO-FCE-VALIDATION-VERDICT-01 Phase 4 — 자동매매 전환 게이트 판정.

## 이 모듈이 하는 일과 하지 않는 일

**한다**: 6축의 현재 상태를 읽어서 보고한다.
**하지 않는다**: 무엇도 해제하지 않는다. 이 모듈에는 `LiveBroker` 로 가는 경로가 없고,
설정을 바꾸는 코드도 없다. 반환값은 순수한 판정이다.

> 봉인(C5)은 `app/core/config.py::reject_unsealed_stock_live_trading` 이 지킨다.
> 이 모듈은 그 봉인을 **관측**할 뿐 건드리지 않는다.

## 왜 결과보다 먼저 만들었나

최종 목표는 4주 검증 후 자동매매인데, 전환 조건이 한 번도 문서로 확정된 적이 없었다.
결과를 보고 조건을 정하면 "이 정도면 됐다"가 조건이 된다 — 이 프로젝트가 통계 레일과
Improvement Proof 로 막아온 바로 그 행위다. 그래서 실측을 보기 **전에** 축과 임계를 박았다.

정본: `docs/validation/LIVE_TRADING_GATE.md`

## GATE_APPROVED

사용자가 문서의 서명란을 채우고 이 상수를 뒤집기 전까지 축 6(인적 승인)은 항상 미충족이고,
따라서 `ready` 는 항상 False 다. **코드가 스스로 이 값을 바꾸는 경로는 없다.**
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.backtest.statistics import bootstrap_win_ci
from app.review.loss_attribution import LOSS_REASON_LABELS, classify_loss
from app.validation import sample_viability

# 사용자 서명 전까지 게이트는 통과할 수 없다. 서명은 문서 커밋으로 남긴다(구두 승인은 승인이 아니다).
GATE_APPROVED = False

# ── 제안 임계 (사용자 확정 전) ──────────────────────────────────────────
# 이 값들은 `docs/validation/LIVE_TRADING_GATE.md` §5 서명란과 짝이다. 승인 전에는 어차피
# 축 6이 막고 있으므로 값 자체가 게이트를 통과시키지 못한다 — 그래도 실측은 이 값 기준으로 낸다.

# 축 3: 판단 결함 계열이 손실에서 차지하는 비중 상한(%).
# 손실이 "무효화선이 옳았음" 중심이면 설계대로 난 손실이다. "진입이 늦음"·"구조 오독"
# 중심이면 판단 자체가 틀린 것이고, 그 상태로 실거래에 가면 같은 오판을 돈으로 반복한다.
MAX_JUDGMENT_FAULT_SHARE_PCT = 40.0
JUDGMENT_FAULT_REASONS = frozenset({"entry_too_late", "structure_misread", "invalidation_too_tight"})

# 축 4: 최근 이 기간의 커버리지가 안정적이어야 한다. 관측이 끊기면 판단 근거가 끊긴 것이다.
STABILITY_WINDOW_DAYS = 14
MIN_STABILITY_COVERAGE_PCT = 95.0
MAX_STABILITY_LOST_DAYS = 1

AXIS_VALIDATION = "validation_complete"
AXIS_EDGE = "performance_edge"
AXIS_LOSS_HEALTH = "loss_attribution_health"
AXIS_STABILITY = "operational_stability"
AXIS_TRACK_SCOPE = "track_independent"
AXIS_HUMAN = "human_approval"

# 측정 축만 진행도의 분모다. 정책 선언(축 5)을 분모에 넣으면 진행도가 공짜로 올라간다.
MEASURED_AXES = (AXIS_VALIDATION, AXIS_EDGE, AXIS_LOSS_HEALTH, AXIS_STABILITY)


@dataclass(frozen=True)
class BenchmarkSpec:
    """축 2의 대조군. 트랙마다 다르고, 정의되지 않은 트랙은 미배선으로 남긴다."""

    label: str
    wired: bool
    reason: str


BENCHMARKS: dict[str, BenchmarkSpec] = {
    "crypto": BenchmarkSpec("사용자 실계좌 거래 (4주 대결)", True, ""),
    "stock_kr": BenchmarkSpec("지수 프록시 ETF", False, "승패 시퀀스 정의 필요 — 지수 수익률은 거래 단위가 아니다"),
    "stock_us": BenchmarkSpec("지수 프록시 ETF", False, "승패 시퀀스 정의 필요 — 지수 수익률은 거래 단위가 아니다"),
    "poly": BenchmarkSpec("시장 내재 확률 대비 Brier", False, "대조 Brier 산출 미배선"),
}


def _axis(
    key: str,
    label: str,
    *,
    kind: str,
    met: bool,
    detail: str,
    current: Any = None,
    target: Any = None,
    available: bool = True,
) -> dict[str, Any]:
    return {
        "axis": key,
        "label": label,
        "kind": kind,
        "met": bool(met),
        "available": available,
        "current": current,
        "target": target,
        "detail": detail,
    }


def _validation_axis(connection: sqlite3.Connection, track: str, *, now: datetime) -> dict[str, Any]:
    completion = sample_viability.validation_completion(connection, track, now=now)
    conditions = completion["conditions"]
    return _axis(
        AXIS_VALIDATION,
        "검증 완료",
        kind="measured",
        met=bool(completion["complete"]),
        current=(
            f"유효일 {conditions['effective_days']['current']}/{conditions['effective_days']['target']}"
            f" · 표본 {conditions['scored_samples']['current']}/{conditions['scored_samples']['target']}"
            f" · 국면 {conditions['regimes']['current']}/{conditions['regimes']['target']}"
        ),
        target="3조건 전부 충족",
        detail=completion["line"],
    )


def _wins_from_paper_trades(connection: sqlite3.Connection) -> list[bool]:
    try:
        rows = connection.execute("SELECT payload FROM paper_trades WHERE status='closed' AND exit_at IS NOT NULL").fetchall()
    except sqlite3.OperationalError:
        return []
    wins: list[bool] = []
    for row in rows:
        payload = _payload(row)
        pnl = payload.get("net_pnl_usdt")
        if isinstance(pnl, (int, float)):
            wins.append(float(pnl) > 0)
    return wins


def _wins_from_user_trades(connection: sqlite3.Connection) -> list[bool]:
    try:
        rows = connection.execute("SELECT payload FROM user_trades").fetchall()
    except sqlite3.OperationalError:
        return []
    wins: list[bool] = []
    for row in rows:
        payload = _payload(row)
        pnl = payload.get("net_pnl_usdt", payload.get("realized_pnl_usdt"))
        if isinstance(pnl, (int, float)):
            wins.append(float(pnl) > 0)
    return wins


def _payload(row: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(row["payload"] if isinstance(row, sqlite3.Row) else row[0]))
    except (ValueError, TypeError, IndexError, KeyError):
        return {}
    return value if isinstance(value, dict) else {}


def edge_verdict(engine_wins: list[bool], benchmark_wins: list[bool]) -> dict[str, Any]:
    """엔진 우위를 **CI 비겹침**으로만 판정한다 (축 2).

    승률 단독 수치로 우위를 주장하지 않는다. 두 구간이 겹치면 "우위 없음"이 아니라
    **"판별 불가"** 다 — 표본이 부족해 구분이 안 된다는 뜻이며, 그 상태로 실거래에 가면
    운을 실력으로 오인한 것이다.
    """
    engine_ci = bootstrap_win_ci(engine_wins) if engine_wins else None
    benchmark_ci = bootstrap_win_ci(benchmark_wins) if benchmark_wins else None
    if engine_ci is None or benchmark_ci is None:
        return {
            "met": False,
            "verdict": "표본 부족",
            "engine_n": len(engine_wins),
            "benchmark_n": len(benchmark_wins),
            "engine_ci": list(engine_ci) if engine_ci else None,
            "benchmark_ci": list(benchmark_ci) if benchmark_ci else None,
        }
    superior = engine_ci[0] > benchmark_ci[1]
    inferior = engine_ci[1] < benchmark_ci[0]
    verdict = "우위 (CI 비겹침)" if superior else "열위 (CI 비겹침)" if inferior else "판별 불가 (CI 겹침)"
    return {
        "met": superior,
        "verdict": verdict,
        "engine_n": len(engine_wins),
        "benchmark_n": len(benchmark_wins),
        "engine_ci": list(engine_ci),
        "benchmark_ci": list(benchmark_ci),
    }


def _edge_axis(connection: sqlite3.Connection, track: str) -> dict[str, Any]:
    spec = BENCHMARKS.get(track)
    if spec is None or not spec.wired:
        reason = spec.reason if spec else "벤치마크 미정의"
        return _axis(
            AXIS_EDGE,
            "성과 우위",
            kind="measured",
            met=False,
            available=False,
            current=None,
            target="CI 비겹침 우위",
            # 알 수 없는 것을 충족으로 처리하면 게이트가 무의미해진다.
            detail=f"벤치마크 미배선 — {reason}",
        )
    result = edge_verdict(_wins_from_paper_trades(connection), _wins_from_user_trades(connection))
    return _axis(
        AXIS_EDGE,
        "성과 우위",
        kind="measured",
        met=bool(result["met"]),
        current=f"{result['verdict']} · 엔진 N={result['engine_n']} / 벤치마크 N={result['benchmark_n']}",
        target="CI 비겹침 우위",
        detail=f"{spec.label} 대비 · 엔진 CI {result['engine_ci']} vs 벤치마크 CI {result['benchmark_ci']}",
    )


def loss_health(connection: sqlite3.Connection, *, ceiling_pct: float = MAX_JUDGMENT_FAULT_SHARE_PCT) -> dict[str, Any]:
    """패인 분포에서 판단 결함 계열이 차지하는 비중 (축 3).

    분류는 **읽는 시점에** 한다. `classify_loss` 는 순수 분류기이며 원장 기록 경로에
    배선돼 있지 않다(`paper_trades.loss_tags` 에는 시그니처·청산사유·레짐만 실린다).
    여기서 원장을 다시 읽어 분류하므로 과거 거래도 같은 잣대로 세어진다.

    `post_exit_extreme`(청산 후 되돌림)은 원장에 없으므로 넘기지 않는다 —
    `invalidation_too_tight` 는 그만큼 과소 계상되고, 그 방향은 **보수적**이다
    (판단 결함 비중을 부풀리지 않는다). 모르는 것을 단정하지 않는다.

    표본 0이면 숫자를 만들지 않는다 — 분류 대상 손실이 없는 것과 건전한 것은 다르다.
    """
    try:
        rows = connection.execute("SELECT payload FROM paper_trades WHERE status='closed'").fetchall()
    except sqlite3.OperationalError:
        rows = []
    counts: dict[str, int] = {}
    for row in rows:
        reason = classify_loss(_payload(row))
        if reason in LOSS_REASON_LABELS:
            counts[reason] = counts.get(reason, 0) + 1
    total = sum(counts.values())
    if total == 0:
        return {"total": 0, "judgment_fault_share_pct": None, "counts": counts, "met": False, "reason": "분류된 손실 0건 — 판정 불가"}
    fault = sum(count for reason, count in counts.items() if reason in JUDGMENT_FAULT_REASONS)
    share = round(fault / total * 100.0, 1)
    return {
        "total": total,
        "judgment_fault_count": fault,
        "judgment_fault_share_pct": share,
        "counts": counts,
        "met": share <= ceiling_pct,
        "reason": None if share <= ceiling_pct else f"판단 결함 비중 {share}% > 상한 {ceiling_pct}%",
    }


def _loss_axis(connection: sqlite3.Connection, track: str) -> dict[str, Any]:
    if track != "crypto":
        return _axis(
            AXIS_LOSS_HEALTH,
            "패인 분포 건전성",
            kind="measured",
            met=False,
            available=False,
            target=f"판단 결함 ≤ {MAX_JUDGMENT_FAULT_SHARE_PCT}%",
            detail="패인 분류가 크립토 원장(paper_trades.loss_tags)에만 배선돼 있다",
        )
    health = loss_health(connection)
    share = health["judgment_fault_share_pct"]
    return _axis(
        AXIS_LOSS_HEALTH,
        "패인 분포 건전성",
        kind="measured",
        met=bool(health["met"]),
        current=f"{share}%" if share is not None else None,
        target=f"≤ {MAX_JUDGMENT_FAULT_SHARE_PCT}%",
        detail=health["reason"] or f"판단 결함 {health.get('judgment_fault_count')}건 / 분류 손실 {health['total']}건",
    )


def stability(connection: sqlite3.Connection, track: str, *, now: datetime, window_days: int = STABILITY_WINDOW_DAYS) -> dict[str, Any]:
    """최근 창의 관측 안정성 (축 4). 호스트 문제가 재발하지 않았는가."""
    since = (now - timedelta(days=window_days)).date().isoformat()
    try:
        rows = connection.execute(
            "SELECT coverage_pct, valid FROM observation_coverage WHERE track=? AND day >= ? AND trading_day=1",
            (track, since),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    if not rows:
        return {"days": 0, "average_coverage_pct": None, "lost_days": None, "met": False, "reason": f"최근 {window_days}일 커버리지 기록 없음"}
    coverages = [float(row["coverage_pct"] or 0) for row in rows]
    lost = sum(1 for row in rows if int(row["valid"] or 0) != 1)
    average = round(sum(coverages) / len(coverages), 1)
    met = average >= MIN_STABILITY_COVERAGE_PCT and lost <= MAX_STABILITY_LOST_DAYS
    return {
        "days": len(rows),
        "average_coverage_pct": average,
        "lost_days": lost,
        "met": met,
        "reason": None if met else f"평균 {average}% (하한 {MIN_STABILITY_COVERAGE_PCT}%) · 유실 {lost}일 (허용 {MAX_STABILITY_LOST_DAYS}일)",
    }


def _stability_axis(connection: sqlite3.Connection, track: str, *, now: datetime) -> dict[str, Any]:
    result = stability(connection, track, now=now)
    return _axis(
        AXIS_STABILITY,
        "운영 안정성",
        kind="measured",
        met=bool(result["met"]),
        current=(f"평균 {result['average_coverage_pct']}% · 유실 {result['lost_days']}일" if result["average_coverage_pct"] is not None else None),
        target=f"최근 {STABILITY_WINDOW_DAYS}일 평균 ≥ {MIN_STABILITY_COVERAGE_PCT}% · 유실 ≤ {MAX_STABILITY_LOST_DAYS}일",
        detail=result["reason"] or "최근 관측 안정",
    )


def live_trading_readiness(
    connection: sqlite3.Connection,
    track: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """트랙 하나의 6축 현재 상태 (Phase 4-4).

    **판정만 한다.** 반환값 어디에도 해제 경로가 없고, 이 함수는 설정을 쓰지 않는다.
    트랙별로 독립 판정한다 — 한 트랙 통과가 다른 트랙을 해제하지 않는다(C4).
    """
    now = now or datetime.now(timezone.utc)
    axes = [
        _validation_axis(connection, track, now=now),
        _edge_axis(connection, track),
        _loss_axis(connection, track),
        _stability_axis(connection, track, now=now),
        _axis(
            AXIS_TRACK_SCOPE,
            "트랙 독립",
            kind="policy",
            met=True,
            current=track,
            target="해당 트랙만 해제",
            detail="통과해도 이 트랙만 대상이다. 통합 단일 해제는 금지된다.",
        ),
        _axis(
            AXIS_HUMAN,
            "인적 승인",
            kind="human",
            met=GATE_APPROVED,
            current="미승인" if not GATE_APPROVED else "승인",
            target="사용자 서명 (docs/validation/LIVE_TRADING_GATE.md §5)",
            detail=("서명란이 비어 있다 — 게이트는 통과할 수 없다" if not GATE_APPROVED else "서명 확인됨"),
        ),
    ]
    measured = [axis for axis in axes if axis["axis"] in MEASURED_AXES]
    met_count = sum(1 for axis in measured if axis["met"])
    unmet = [axis["label"] for axis in axes if not axis["met"]]
    return {
        "track": track,
        "axes": axes,
        "measured_met": met_count,
        "measured_total": len(measured),
        "progress_pct": round(met_count / len(measured) * 100, 1) if measured else 0.0,
        # 측정 축 전부 + 인적 승인이어야 준비 완료다. 정책 축은 분모가 아니라 선언이다.
        "ready": met_count == len(measured) and GATE_APPROVED,
        "unmet": unmet,
        "gate_approved": GATE_APPROVED,
        # 이 판정은 어떤 봉인도 풀지 않는다. 사실을 응답에 박아 오해를 차단한다.
        "seal": "LiveBroker 미구현 · 이 판정은 아무것도 해제하지 않는다 (C5)",
        "document": "docs/validation/LIVE_TRADING_GATE.md",
        "line": f"{track} 게이트 {met_count}/{len(measured)} 측정 축 통과" + ("" if GATE_APPROVED else " · 인적 승인 미완"),
    }


def live_trading_gate_report(connection: sqlite3.Connection, *, now: datetime | None = None) -> dict[str, Any]:
    """4트랙 전체 게이트 진행도."""
    now = now or datetime.now(timezone.utc)
    tracks = {track: live_trading_readiness(connection, track, now=now) for track in sample_viability.TRACK_SAMPLE_SPECS}
    return {
        "as_of": now.isoformat(),
        "gate_approved": GATE_APPROVED,
        "principle": "결과를 보기 전에 정한 기준만이 기준이다. 이 판정은 아무것도 해제하지 않는다.",
        "document": "docs/validation/LIVE_TRADING_GATE.md",
        "tracks": tracks,
        "ready_tracks": [track for track, row in tracks.items() if row["ready"]],
    }


__all__ = [
    "AXIS_EDGE",
    "AXIS_HUMAN",
    "AXIS_LOSS_HEALTH",
    "AXIS_STABILITY",
    "AXIS_TRACK_SCOPE",
    "AXIS_VALIDATION",
    "GATE_APPROVED",
    "JUDGMENT_FAULT_REASONS",
    "MAX_JUDGMENT_FAULT_SHARE_PCT",
    "MEASURED_AXES",
    "MIN_STABILITY_COVERAGE_PCT",
    "STABILITY_WINDOW_DAYS",
    "edge_verdict",
    "live_trading_gate_report",
    "live_trading_readiness",
    "loss_health",
    "stability",
]
