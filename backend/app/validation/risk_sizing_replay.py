"""WO-FCE-RISK-SIZING-01 Phase 4 — 사이징·잠금·상한 반사실의 **재현 가능한** 기반.

## 왜 이 모듈이 존재하는가

Phase 1·2·3 의 반사실은 전부 **커밋되지 않은 임시 스크립트**로 산출됐다. 그래서 Phase 4
착수 시점에 Phase 3-5 가 발표한 합계(`netR −0.995` · `PF 0.9109` · `MDD 13.95`)를
**재현할 수 없었다.** 모집단(N=33 → 잠금 24)과 차단 9건의 값(gross −0.914R ·
비용 1.127R)은 정확히 재현되는데 합계만 일정한 오프셋만큼 어긋난다
(gross −2.205R · 비용 −0.303R · net −1.902R — 현행·잠금 두 행에서 **동일**).

원인은 확정할 수 없다 — **읽을 스크립트가 없기 때문이다.** 그것이 이 모듈의 존재 이유다.
숫자를 발표하는 경로는 코드로 남아야 하고, 다음 Phase 가 그것을 실행해 같은 값을 얻어야
한다. 여기 있는 정의가 정본이며, `docs/validation/POSITION_SIZING.md` 가 그 결과를 인용한다.

## R 은 사이즈에 불변이다 (Phase 1 실측)

수량이 s 배가 되면 손익·비용·계획 리스크가 모두 s 배가 되므로

    R = 손익 / 계획리스크,   계획리스크 = |진입가 − 무효화가| × 수량

는 `fixed_notional` 원장에서 계산해도 `risk_based` 반사실과 **같은 값**이다. 그래서 이
모듈은 수량을 재척도하지 않고 저장된 원장에서 직접 R 을 낸다. 금액 지표(PF·MDD)만
`risk_based` 의 1R 금액(= 리스크 예산)을 곱해 환산한다 — 상한에 걸리지 않는 한 1R 금액이
예산 상수이기 때문이다(표본 최대 명목 308 < 상한 600 이라 상한 미발동).

## 지표 정의는 `_metric_payload` 와 같다 (METRIC-TRUTH-01 결정 1·3)

- **PF** = Σmax(0, net) / |Σmin(0, net)| — 같은 모집단 위에서
- **MDD** = 금액 곡선(청산 순)의 최대 낙폭. 퍼센트 곡선이 아니다
- 모집단은 하나다. `time_decay`·`time_stop` 을 계수에서 빼지 않는다
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Iterable, Sequence


# Phase 1 채택값. 금액 환산에만 쓰며 R 계산에는 관여하지 않는다.
DEFAULT_RISK_BUDGET_USDT = 2.5

# 손절 체결 사유. Phase 2 의 N=8 모집단과 같은 술어다.
STOP_EXIT_REASON = "invalidation_breach"

# 원장에서 계수 대상이 아닌 건 (service.`_is_pre_tp_pressure_exit` · 중복 부트스트랩 억제).
_INVALID_TAG_PREFIX = "policy_invalid:"
_SUPPRESSED_TAG = "duplicate_bootstrap_suppressed"


@dataclass(frozen=True)
class ReplayTrade:
    """반사실 계산에 필요한 최소 필드만 뽑은 거래 한 건."""

    trade_id: str
    symbol: str
    timeframe: str
    direction: str
    entry_bar_at: datetime
    exit_bar_at: datetime | None
    entry_price: float
    invalidation_price: float
    exit_price: float | None
    exit_reason: str | None
    quantity: float
    gross_pnl_usdt: float
    costs_usdt: float
    net_pnl_usdt: float

    @property
    def stop_distance(self) -> float:
        return abs(self.entry_price - self.invalidation_price)

    @property
    def planned_risk_usdt(self) -> float:
        """1R 의 금액가치. `fixed_notional` 원장 기준이며 R 비율에는 영향이 없다."""
        return self.stop_distance * self.quantity

    @property
    def stop_distance_pct(self) -> float:
        return self.stop_distance / self.entry_price * 100.0 if self.entry_price > 0 else 0.0

    @property
    def gross_r(self) -> float:
        return self.gross_pnl_usdt / self.planned_risk_usdt

    @property
    def cost_r(self) -> float:
        return self.costs_usdt / self.planned_risk_usdt

    @property
    def net_r(self) -> float:
        return self.net_pnl_usdt / self.planned_risk_usdt

    @property
    def is_stop_exit(self) -> bool:
        return self.exit_reason == STOP_EXIT_REASON


@dataclass(frozen=True)
class Metrics:
    """한 모집단의 성과. 정의는 `paper/service._metric_payload` 와 일치한다."""

    sample_size: int
    gross_r: float
    cost_r: float
    net_r: float
    profit_factor: float | None
    mdd_usdt: float
    net_pnl_usdt: float
    mean_stop_distance_pct: float
    edge_to_cost_ratio: float | None

    def as_row(self, label: str) -> str:
        pf = "n/a" if self.profit_factor is None else f"{self.profit_factor:.4f}"
        ratio = "n/a" if self.edge_to_cost_ratio is None else f"{self.edge_to_cost_ratio:.2f}배"
        return (
            f"{label:<26} N={self.sample_size:>3}  gross={self.gross_r:+7.3f}R  "
            f"비용={self.cost_r:6.3f}R  net={self.net_r:+7.3f}R  우위/비용={ratio:>7}  "
            f"PF={pf:>6}  MDD={self.mdd_usdt:6.2f}  평균스톱={self.mean_stop_distance_pct:.3f}%"
        )


def load_trades(database_path: str) -> list[ReplayTrade]:
    """계수 대상 거래를 **읽기 전용**으로 읽는다. 이 모듈은 DB 에 쓰지 않는다.

    열린 건·정책 무효 건·중복 부트스트랩 억제 건을 뺀다 — `_paper_metrics` 와 같은 술어다.
    """
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        payloads = [json.loads(row[0]) for row in connection.execute("SELECT payload FROM paper_trades")]
    finally:
        connection.close()
    return [trade for trade in (_replay_trade(item) for item in payloads) if trade is not None]


def same_bar_reentry_blocked(trades: Sequence[ReplayTrade], *, same_direction_only: bool = True) -> set[str]:
    """`reentry_lock_mode="same_bar"` 로 차단될 거래의 id 집합 (Phase 3-2 채택값).

    라이브 `_last_exit_for` 는 **기록된 전체 이력의 최근 청산**을 본다 — 잠금이 걸려
    없었을 거래까지 포함한 시야다. 반사실도 같은 시야로 낸다(순차 가지치기 금지):
    같은 심볼·타임프레임·방향에서 **다른 거래의 청산봉 == 이 거래의 진입봉**이면 차단.

    실측 재현: 차단 9건 · gross −0.914R · 비용 1.127R (`crypto-v2.json` 근거값과 일치).
    """
    blocked: set[str] = set()
    for candidate in trades:
        for other in trades:
            if other.trade_id == candidate.trade_id:
                continue
            if other.symbol != candidate.symbol or other.timeframe != candidate.timeframe:
                continue
            if same_direction_only and other.direction != candidate.direction:
                continue
            if other.exit_bar_at is not None and other.exit_bar_at == candidate.entry_bar_at:
                blocked.add(candidate.trade_id)
                break
    return blocked


def metrics(
    trades: Sequence[ReplayTrade],
    *,
    risk_budget_usdt: float = DEFAULT_RISK_BUDGET_USDT,
    one_way_cost_pct: float | None = None,
) -> Metrics:
    """모집단 지표. `one_way_cost_pct` 를 주면 비용을 그 전제로 **재계산**한다.

    비용 재계산은 `비용R = 왕복 비용률 / 스톱거리%` 를 쓴다(Phase 3-5 §3). 사이즈와
    무관하므로 민감도표를 만들 때 이 경로를 쓴다. 주지 않으면 **원장의 실제 비용**을 쓴다.
    """
    ordered = sorted(trades, key=lambda item: (item.exit_bar_at or item.entry_bar_at, item.trade_id))
    if not ordered:
        return Metrics(0, 0.0, 0.0, 0.0, None, 0.0, 0.0, 0.0, None)

    gross_r = sum(item.gross_r for item in ordered)
    if one_way_cost_pct is None:
        cost_per_trade = [item.cost_r for item in ordered]
    else:
        round_trip = one_way_cost_pct * 2.0
        cost_per_trade = [round_trip / item.stop_distance_pct if item.stop_distance_pct > 0 else 0.0 for item in ordered]
    cost_r = sum(cost_per_trade)
    net_per_trade = [item.gross_r - cost for item, cost in zip(ordered, cost_per_trade)]
    net_r = sum(net_per_trade)

    profit = sum(value for value in net_per_trade if value > 0)
    loss = abs(sum(value for value in net_per_trade if value < 0))
    profit_factor = profit / loss if loss > 0 else None

    equity = peak = mdd = 0.0
    for value in net_per_trade:
        equity += value * risk_budget_usdt
        peak = max(peak, equity)
        mdd = max(mdd, peak - equity)

    return Metrics(
        sample_size=len(ordered),
        gross_r=gross_r,
        cost_r=cost_r,
        net_r=net_r,
        profit_factor=profit_factor,
        mdd_usdt=mdd,
        net_pnl_usdt=net_r * risk_budget_usdt,
        mean_stop_distance_pct=sum(item.stop_distance_pct for item in ordered) / len(ordered),
        edge_to_cost_ratio=gross_r / cost_r if cost_r else None,
    )


# ---------------------------------------------------------------------------
# 4-2 갭 예산
# ---------------------------------------------------------------------------

# 갭 판정 임계. 초과분이 **계획 리스크 1R 을 넘는** 건을 갭으로 본다.
#
# 초과R = |체결가 − 무효화가| / 스톱거리 이므로 이 임계는 "한 봉이 스톱 거리 전체보다
# 더 멀리 무효화선을 지나쳤다"와 같은 말이다. 종가 체결 규칙만으로는 그 크기가 나오지
# 않는다. 실측 8건에서 SPCXUSDT 2.510 vs 나머지 최대 0.490 으로 **깨끗이 갈린다**.
#
# ⚠️ N=8 에서 고른 임계다. 표본이 늘면 재검토 대상이다.
GAP_EXCESS_R_THRESHOLD = 1.0


@dataclass(frozen=True)
class StopExecution:
    """손절 체결 한 건의 실효 리스크.

    **두 기준을 함께 낸다 — Phase 2 가 둘을 섞어 썼기 때문이다.**

    - `effective_risk_r` (net): 실제로 잃은 금액 / 1R 금액. 예산은 금액이므로 **예산 대조는
      이 값**이다. Phase 2 의 "예산 2.5 → 실제 4.20 (1.68배)" 가 이 기준이다.
    - `execution_risk_r` (gross): 마찰 비용을 뺀 체결가 이탈만. Phase 2 의 건별 표
      (SPCX 초과 +2.510R · 나머지 +0.280R · 56%)가 이 기준이다.

    비용은 슬리피지·수수료 소관이고(`EXECUTION_MODEL.md`) 이탈은 체결 규칙 소관이라
    수리 대상이 다르다. 그래서 한 숫자로 합치지 않는다.
    """

    trade: ReplayTrade
    overshoot_pct: float
    excess_r: float
    execution_risk_r: float
    effective_risk_r: float
    is_gap: bool

    @property
    def effective_risk_usdt(self) -> float:
        return self.effective_risk_r * DEFAULT_RISK_BUDGET_USDT


def stop_executions(trades: Iterable[ReplayTrade]) -> list[StopExecution]:
    """손절 체결 건의 실효 리스크 (4-2 작업 1·2).

    **실효 리스크는 손절이 걸린 거래에서만 관측된다.** 익절·시간 청산 건은 계획 리스크가
    시험된 적이 없으므로 실효 리스크를 알 수 없다 — 1.0 으로 채우지 않는다(C10).
    """
    rows: list[StopExecution] = []
    for trade in trades:
        if not trade.is_stop_exit or trade.exit_price is None or trade.invalidation_price <= 0:
            continue
        overshoot = abs(trade.exit_price - trade.invalidation_price)
        execution_risk = abs(trade.gross_r)
        excess = execution_risk - 1.0
        rows.append(
            StopExecution(
                trade=trade,
                overshoot_pct=overshoot / trade.invalidation_price * 100.0,
                excess_r=excess,
                execution_risk_r=execution_risk,
                effective_risk_r=abs(trade.net_r),
                is_gap=excess > GAP_EXCESS_R_THRESHOLD,
            )
        )
    return sorted(rows, key=lambda item: item.excess_r, reverse=True)


@dataclass(frozen=True)
class GapBudget:
    """갭을 반영한 실효 리스크 요약. 예산 값은 바꾸지 않는다 — 추가 산출이다(C3)."""

    sample_size: int
    planned_risk_usdt: float
    mean_effective_risk_r: float
    mean_effective_risk_usdt: float
    multiple: float
    mean_execution_risk_r: float
    execution_multiple: float
    gap_count: int
    gap_mean_excess_r: float
    close_rule_count: int
    close_rule_mean_excess_r: float
    gap_share_of_excess_pct: float


def gap_budget(rows: Sequence[StopExecution], *, risk_budget_usdt: float = DEFAULT_RISK_BUDGET_USDT) -> GapBudget | None:
    if not rows:
        return None
    gaps = [item for item in rows if item.is_gap]
    close_rule = [item for item in rows if not item.is_gap]
    total_excess = sum(item.excess_r for item in rows)
    mean_effective = sum(item.effective_risk_r for item in rows) / len(rows)
    mean_execution = sum(item.execution_risk_r for item in rows) / len(rows)
    return GapBudget(
        sample_size=len(rows),
        planned_risk_usdt=risk_budget_usdt,
        mean_effective_risk_r=mean_effective,
        mean_effective_risk_usdt=mean_effective * risk_budget_usdt,
        multiple=mean_effective,
        mean_execution_risk_r=mean_execution,
        execution_multiple=mean_execution,
        gap_count=len(gaps),
        gap_mean_excess_r=(sum(item.excess_r for item in gaps) / len(gaps)) if gaps else 0.0,
        close_rule_count=len(close_rule),
        close_rule_mean_excess_r=(sum(item.excess_r for item in close_rule) / len(close_rule)) if close_rule else 0.0,
        gap_share_of_excess_pct=(sum(item.excess_r for item in gaps) / total_excess * 100.0) if total_excess else 0.0,
    )


def gap_distribution(rows: Sequence[StopExecution]) -> dict[str, list[dict[str, Any]]]:
    """갭 빈도·크기 분포 (4-2 작업 4). 심볼별·시간대별(UTC 진입 시각)."""
    by_symbol: dict[str, list[StopExecution]] = {}
    by_hour: dict[int, list[StopExecution]] = {}
    for item in rows:
        by_symbol.setdefault(item.trade.symbol, []).append(item)
        by_hour.setdefault(item.trade.entry_bar_at.astimezone(timezone.utc).hour, []).append(item)

    def summarize(bucket: Sequence[StopExecution]) -> dict[str, Any]:
        return {
            "stops": len(bucket),
            "gaps": sum(1 for item in bucket if item.is_gap),
            "mean_excess_r": sum(item.excess_r for item in bucket) / len(bucket),
            "max_excess_r": max(item.excess_r for item in bucket),
            "max_overshoot_pct": max(item.overshoot_pct for item in bucket),
        }

    return {
        "by_symbol": [{"key": key, **summarize(bucket)} for key, bucket in sorted(by_symbol.items())],
        "by_entry_hour_utc": [{"key": f"{key:02d}:00", **summarize(bucket)} for key, bucket in sorted(by_hour.items())],
    }


# ---------------------------------------------------------------------------
# 4-3 포트폴리오 상한
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PortfolioCaps:
    """상한 3안. `None` 은 그 축을 걸지 않는다는 뜻이다."""

    label: str
    max_total_risk_usdt: float | None = None
    max_same_direction_positions: int | None = None
    max_cluster_positions: int | None = None
    cluster_of: dict[str, str] = field(default_factory=dict)

    def cluster(self, symbol: str) -> str:
        return self.cluster_of.get(symbol.upper(), "unclustered")


@dataclass(frozen=True)
class CapReplay:
    kept: list[ReplayTrade]
    blocked: list[tuple[ReplayTrade, str]]


def apply_portfolio_caps(
    trades: Sequence[ReplayTrade],
    caps: PortfolioCaps,
    *,
    risk_budget_usdt: float = DEFAULT_RISK_BUDGET_USDT,
) -> CapReplay:
    """상한을 시간순으로 적용한다. 보류된 진입은 **사유와 함께** 돌려준다 (C11).

    동시 보유는 진입봉~청산봉 구간의 겹침으로 판정한다. 보류된 거래는 이후 판정에서
    보유 중이 아니므로, 뒤 거래의 여유를 만든다 — 실제 엔진과 같은 순차 구조다.
    """
    ordered = sorted(trades, key=lambda item: (item.entry_bar_at, item.trade_id))
    kept: list[ReplayTrade] = []
    blocked: list[tuple[ReplayTrade, str]] = []

    for candidate in ordered:
        held = [item for item in kept if item.exit_bar_at is None or item.exit_bar_at > candidate.entry_bar_at]
        reason: str | None = None

        if caps.max_total_risk_usdt is not None:
            exposure = risk_budget_usdt * (len(held) + 1)
            if exposure > caps.max_total_risk_usdt + 1e-9:
                reason = f"portfolio_cap:total_risk>{caps.max_total_risk_usdt:g}"

        if reason is None and caps.max_same_direction_positions is not None:
            same_direction = sum(1 for item in held if item.direction == candidate.direction)
            if same_direction + 1 > caps.max_same_direction_positions:
                reason = f"portfolio_cap:same_direction>{caps.max_same_direction_positions}"

        if reason is None and caps.max_cluster_positions is not None:
            cluster = caps.cluster(candidate.symbol)
            same_cluster = sum(1 for item in held if caps.cluster(item.symbol) == cluster)
            if same_cluster + 1 > caps.max_cluster_positions:
                reason = f"portfolio_cap:cluster[{cluster}]>{caps.max_cluster_positions}"

        if reason is None:
            kept.append(candidate)
        else:
            blocked.append((candidate, reason))

    return CapReplay(kept=kept, blocked=blocked)


# ---------------------------------------------------------------------------
# 원장 파싱
# ---------------------------------------------------------------------------


def _replay_trade(payload: dict[str, Any]) -> ReplayTrade | None:
    if str(payload.get("status")) != "closed":
        return None
    tags = [str(tag) for tag in (payload.get("loss_tags") or [])]
    if any(tag.startswith(_INVALID_TAG_PREFIX) for tag in tags) or _SUPPRESSED_TAG in tags:
        return None
    entry_bar = _parse(payload.get("entry_bar_at"))
    entry_price = _number(payload.get("entry_price"))
    invalidation = _number(payload.get("invalidation_price"))
    quantity = _number(payload.get("quantity"))
    if entry_bar is None or entry_price is None or invalidation is None or quantity is None:
        return None
    if entry_price <= 0 or quantity <= 0 or abs(entry_price - invalidation) <= 0:
        return None
    return ReplayTrade(
        trade_id=str(payload.get("id")),
        symbol=str(payload.get("symbol") or "").upper(),
        timeframe=str(payload.get("timeframe") or "4h"),
        direction=str(payload.get("direction") or ""),
        entry_bar_at=entry_bar,
        exit_bar_at=_parse(payload.get("exit_bar_at")) or _parse(payload.get("exit_at")),
        entry_price=entry_price,
        invalidation_price=invalidation,
        exit_price=_number(payload.get("exit_price")),
        exit_reason=(str(payload["exit_reason"]) if payload.get("exit_reason") else None),
        quantity=quantity,
        gross_pnl_usdt=_number(payload.get("gross_pnl_usdt")) or 0.0,
        costs_usdt=_number(payload.get("costs_usdt")) or 0.0,
        net_pnl_usdt=_number(payload.get("net_pnl_usdt")) or 0.0,
    )


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
