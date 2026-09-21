from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Sequence
from uuid import UUID

from app.db.models import Direction, MarketCandle, PaperTrade


ExitReason = Literal[
    "invalidation_breach",
    "breakeven_stop",
    "opposite_stance_flip",
    "take_profit_pressure",
    "take_profit_2",
    "time_decay",
    "time_stop",
]


@dataclass(frozen=True)
class PaperPolicy:
    margin_usdt: float = 100.0
    leverage: float = 3.0
    max_open_positions: int = 5
    min_evidence: int = 4
    min_checklist_passed: int = 5
    min_checklist_total: int = 5
    min_rr: float = 1.5
    min_signature_ci_low_pct: float = 50.0
    max_holding_bars: int = 30
    take_profit_atr_k1: float = 1.0
    take_profit_atr_k2: float = 2.0
    take_profit_pressure_bars: int = 2
    taker_fee_pct: float = 0.06
    slippage_pct: float = 0.03
    # WO-FCE-CORE-DEFECTS-01 Phase 1. 기본값은 **기존 동작**이며, crypto-v2.json 이
    # 로드될 때만 새 모드가 적용된다(옵트인 — 파일 없으면 회귀 0).
    version: str = "crypto-v1"
    stance_gate_mode: str = "confirmed_flip"
    signature_gate_mode: str = "required"
    # WO-FCE-RISK-SIZING-01 Phase 1. 기본값은 **기존 동작**(고정 명목)이며 옵트인이다.
    #
    # 고정 명목에서는 수량이 스톱 거리와 무관하게 정해진다. 그래서 1R 의 금액가치가
    # 스톱 거리에 비례하고(실측 CV 0.522 · 최대/최소 6.16배), 스톱 거리가 1.28배 큰
    # 지는 거래가 같은 −1R 로도 더 큰 금액을 잃는다. R 회계와 금액 회계가 어긋난다.
    #
    #   수량 = 리스크 예산 / |진입가 − 무효화가|  →  1R 금액 = 리스크 예산 (상수)
    sizing_mode: str = "fixed_notional"
    risk_budget_usdt: float = 2.5
    max_notional_usdt: float = 600.0
    min_notional_usdt: float = 10.0
    # WO-FCE-RISK-SIZING-01 Phase 3. 기본값은 **기존 동작**(잠금 없음)이며 옵트인이다.
    #
    # 같은 확정봉 안에서 청산하고 곧바로 같은 가격·같은 방향으로 다시 들어가는 일이
    # 실측 9건 있었다. 새 판단이 아니라 왕복이다 — gross 우위가 **음수**(−0.914R)이면서
    # 비용만 1.127R 을 낸다. 막으면 우위/비용 비율이 3.93배 → 1.51배가 된다.
    reentry_lock_mode: str = "off"
    reentry_lock_bars: int = 0
    reentry_lock_same_direction_only: bool = True
    # WO-FCE-RISK-SIZING-01 Phase 3-4. 기본값은 **기존 동작**(gross)이며 옵트인이다.
    #
    # `rr_ratio` 는 비용을 빼기 전 값이다. 스톱 0.8% 거래는 왕복 비용으로 0.22R 을 내므로
    # 게이트가 재는 것(1.5)과 엔진이 얻는 것(약 1.05)이 다르다.
    #
    # ⚠️ `net` 으로 전환하면 진입이 83% 줄어든다 — 게이트가 항등식이기 때문이다.
    #    staged_reward = ATR×1.5 이고 execution_risk = min(structural, ATR) 이라
    #    스톱이 1 ATR 보다 넓으면 RR 이 **산술적으로 정확히 1.5** 가 된다(실측 20/24 건).
    #    비용을 빼면 전부 1.5 아래로 떨어진다. 사용자 결정 전까지 gross 로 둔다.
    rr_basis: str = "gross"
    # WO-FCE-RISK-SIZING-01 Phase 4-3. 기본값은 **기존 동작**(상한 없음)이며 옵트인이다.
    #
    # 지금까지 포트폴리오 제약은 `max_open_positions=5` 하나였다. 5건의 **리스크 합계**
    # 상한도, 방향 편중 상한도, 상관 상한도 없다 — 전 포지션이 같은 방향이면 그것은
    # 분산이 아니라 한 개의 베팅이다.
    #
    # ⚠️ 반사실은 **채택을 지지하지 않는다.** 잠금 적용 N=25 에서 netR 을 개선하는 유일한
    #    설정(총리스크 10)은 거래 **1건**(BASEDUSDT −0.300R)을 막아서 얻은 값이고, MDD 는
    #    17.52 로 전혀 개선되지 않는다. MDD 를 낮추는 설정(총리스크 5 → 13.61)은 netR 을
    #    +0.619 → −0.718 로 무너뜨린다. N=25 에서 1건으로 정책을 정하는 것은 잡음 적합이며
    #    Phase 3 이 긴 잠금을 배제한 것과 같은 이유로 **기본값은 off 로 둔다.**
    #    배선만 하고 표본이 쌓인 뒤 판정한다.
    #
    # `risk_based` 사이징에서 1R 금액이 예산 상수이므로 총리스크 상한은 사실상 슬롯 수
    # 상한과 같다(예산 2.5 × N). 명목 상·하한에 걸려 실제 리스크가 예산과 달라질 때만
    # 두 축이 갈라진다 — 그래서 건수가 아니라 **금액**으로 잰다.
    portfolio_cap_mode: str = "off"
    max_total_risk_usdt: float | None = None
    max_same_direction_positions: int | None = None
    max_correlation_cluster_positions: int | None = None
    # 상관 군집 분류. **측정이 아니라 선언이다** — 근거와 한계는 POSITION_SIZING.md 에 남긴다.
    correlation_clusters: dict[str, str] = field(default_factory=dict)
    # ------------------------------------------------------------------
    # WO-FCE-NET-EDGE-01. 기본값은 **전부 기존 동작**이며 옵트인이다.
    #
    # 재판정 N=427(BTC 204 · ETH 223)이 말한 것: gross 우위 −10.8R · 비용 56.5R ·
    # netR −67.2R. **비용이 우위의 2.9배다.** 그리고 비용R 은 사이즈와 무관한 항등식이다:
    #
    #     비용R = 왕복 비용률 / 스톱거리%        (리스크 기준 사이징에서 1R 금액은 상수)
    #
    # 즉 **스톱을 좁히는 모든 것이 비용을 키운다.** 그런데 현행은 스톱을 1 ATR 로
    # 캡한다(`execution_risk = min(structural_risk, ATR)`). 실측 20/24 건에서 이 캡이
    # 물렸고, 그 결과 스톱거리 1.2~1.6% · 비용R 0.11~0.15 가 됐다. 같은 캡이 RR 을
    # 항등식으로 만든다 — `staged_reward = ATR×1.5` 이므로 RR 이 산술적으로 정확히 1.5 다.
    #
    # 세 축은 같은 뿌리를 공유한다. 그래서 같은 WO 에서 고치되 **축은 분리해 둔다** —
    # 하나씩 켜고 끄며 재판정 스윕으로 축별 기여를 가를 수 있어야 한다(AGENTS.md).
    # ------------------------------------------------------------------
    #
    # `atr_capped`(기본): 기존 동작. 구조 무효화가 1 ATR 보다 멀면 **조용히 1 ATR 로 좁힌다.**
    # `structural`: 논제 무효화를 그대로 쓴다. 좁히지 않는다 — 경계를 벗어나면 **거부**한다.
    # `nearest_structure`: 논제 무효화 하나만 보지 않는다. 그 봉의 구조 레벨(지지/저항/무효화
    #     후보) 중 **손절 쪽에 있으면서 하한을 넘는 가장 가까운 것**을 고른다.
    #
    #     왜 필요한가: `action_plan` 의 무효화는 `invalidation_levels[0]` 또는 `support[0]`
    #     이며 **근접 순이 아니다.** 픽스처 실측에서 그 거리가 6~19 ATR 로 나온다 — 손절이
    #     아니라 "가격이 반토막 나야 틀린 것"이다. 1 ATR 캡은 그 거리를 쓸 수 있게 만들려고
    #     덧댄 반창고였고, 그 반창고가 RR 항등식·노이즈 손절·과다 마찰을 낳았다.
    #     같은 레벨 목록에서 **가장 가까운 유효 레벨**을 고르면 반창고 없이 성립한다.
    #
    #     새 감지기가 아니다 — 기존 구조 레벨을 소비할 뿐이며 방향 판정에는 관여하지 않는다.
    risk_mode: str = "atr_capped"
    # 구조 스톱이 이 배수보다 가까우면 **넓힌다**(비용R 감소 · 1R 금액은 예산 상수라 불변).
    min_stop_atr_multiple: float | None = None
    # 구조 스톱이 이 배수보다 멀면 **거부한다**(좁히지 않는다).
    max_stop_atr_multiple: float | None = None
    #
    # `atr_ladder`(기본): TP1 = k1×ATR · TP2 = k2×ATR. 구조 목표는 **줄이는 방향으로만**
    #                     반영된다(`tp2_distance = structural` 은 더 가까울 때만).
    # `risk_multiple`: 사다리를 **리스크의 배수**로 잡는다. 구조 목표는 경계 안에서 늘릴 수도
    #                  있다. 이때 RR 은 더 이상 상수가 아니라 셋업 품질의 함수가 된다.
    # `structural_extend`: TP1·TP2 는 ATR 사다리 그대로 두되, 구조 목표가 TP2 **너머**에 있으면
    #     상한까지 늘려 쓴다. 현행은 구조 목표를 **줄이는 방향으로만** 반영한다 — 가까우면
    #     당겨오고 멀면 무시한다. 그래서 추세가 길게 갈 때 그 구간이 계획에 아예 안 들어간다.
    #     늘어난 잔량은 기존 익절 압력 청산이 보호한다(실측 9건 합 +26.87%).
    reward_mode: str = "atr_ladder"
    # `structural_extend` 에서 TP2 가 ATR 의 몇 배를 넘지 못하는가.
    max_reward_atr_multiple: float = 3.5
    take_profit_1_r: float = 1.0
    # TP2 상한. **하한은 두지 않는다** — 최소 배수를 깔면 RR 이 그 배수에 고정돼 항등식이
    # 자리를 옮길 뿐이다. 구조 목표가 가까우면 RR 이 낮게 나오고, 그 판정은 게이트가 한다.
    take_profit_2_r_max: float = 3.5
    # 구조 목표가 **아예 없을 때만** 쓰는 기본 배수.
    take_profit_2_r_default: float = 2.5
    #
    # 마찰 상한. `비용R = 왕복 비용률 / 스톱거리%` 가 이 값을 넘으면 거부한다.
    # 품질 게이트가 아니라 **산술 게이트다** — 보상이 아무리 좋아도 마찰이 1R 의 이만큼을
    # 먹는 자리에서는 우위가 남을 수 없다. `None`(기본)이면 재지 않는다.
    max_entry_cost_r: float | None = None
    # `rr_basis="net"` 일 때 쓰는 하한. `None` 이면 `min_rr` 을 그대로 쓴다 —
    # 총 기준과 순 기준의 임계를 따로 풀 수 있어야 굶주림을 국소 진단할 수 있다.
    min_net_rr: float | None = None
    # 상위 타임프레임 충돌을 **차단 사유로** 쓸 것인가. 기본은 기존 동작(체크리스트 6항목 중
    # 하나로만 셈 — 다른 5항목에 표로 뒤집힌다).
    htf_conflict_blocks: bool = False
    #
    # 손절 체결 시점. `close`(기본)는 봉이 닫힐 때까지 기다렸다 **종가**에 체결한다.
    # 익절은 이미 봉 중간 터치이므로 이 비대칭이 초과 손실의 82% 를 만들었다
    # (실측 평균 −1.559R · 계획은 −1.000R). `intrabar` 는 실제 스톱 주문처럼
    # 봉 중간에 **무효화가로** 체결하고, 봉이 이미 넘어서 열렸으면 **시가로** 체결한다(갭).
    stop_fill_mode: str = "close"

    @property
    def execution_cost_rate(self) -> float:
        return max(0.0, self.taker_fee_pct + self.slippage_pct) / 100.0

    @property
    def roundtrip_cost_rate(self) -> float:
        """왕복 비용률. 진입·청산 두 번 나간다 — 편도의 2배다."""
        return self.execution_cost_rate * 2.0

    @property
    def effective_min_net_rr(self) -> float:
        return self.min_rr if self.min_net_rr is None else self.min_net_rr

    def correlation_cluster(self, symbol: str) -> str:
        return self.correlation_clusters.get(symbol.upper(), "unclustered")


@dataclass(frozen=True)
class EntryDecision:
    enter: bool
    gates: dict[str, bool]
    rejection_reasons: tuple[str, ...]
    # 판정 조건에서 제외했지만 사후 채점을 위해 원장에 남기는 관측치(Phase 1).
    observations: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExitDecision:
    action: Literal["hold", "partial", "close"]
    reason: ExitReason | Literal["take_profit_1", "none"] = "none"
    high_pressure_streak: int = 0
    execution_price: float | None = None


def evaluate_entry(
    *,
    stance_state: dict[str, Any],
    direction: Direction,
    evidence_count: int,
    checklist_passed: int,
    checklist_total: int,
    rr_ratio: float | None,
    invalidation_hygiene: bool = True,
    survives_to_invalidation: bool,
    validated_signature: bool,
    signature_ci_low_pct: float | None,
    earnings_clear: bool,
    data_fresh: bool,
    confirmed_bar: bool,
    policy: PaperPolicy,
    # WO-FCE-NET-EDGE-01. 셋 다 기본값이 **재지 않음**이라 기존 호출자는 회귀 0 이다.
    cost_r: float | None = None,
    stop_atr_multiple: float | None = None,
    htf_conflict: bool = False,
) -> EntryDecision:
    stance = str(stance_state.get("stance") or "")
    stance_direction = "long" if stance in {"long", "long_leaning"} else "short" if stance in {"short", "short_leaning"} else stance
    # Phase 1: flipped 는 **판정 대상이지 판정 조건이 아니다**.
    #
    # 실측(2026-08-05, 600건): confirmed_flip 통과 6.0%. 원인은 WO 가정("flipped 와
    # transitioning 이 겹치는 창이 없다")과 다르다 — 상태머신은 flip 완료 시
    # build(..., transitioning=False, flipped=True) 로 **둘을 동시에** 세우므로 모순이
    # 아니다. 진짜 제약은 flipped=True 가 **flip 완료 봉 1봉만의 펄스**라는 것이다
    # (이후 봉은 cand==prior_stance → flipped=False, 봉 앵커 동결도 False).
    # 즉 진입 기회가 flip 순간 1봉으로 제한됐고, 그 1봉에서 나머지 게이트까지 동시에
    # 통과해야 했다. 이번 주 flip 14회 → 진입 0회가 그 결과다.
    #
    # stable_direction: 방향 일치 + 안정 상태를 요구한다. 주식 stock-v4
    # (stance_gate_mode=stable_long)와 같은 원리를 롱·숏 대칭으로 이식한 것이며,
    # 품질 임계(evidence·checklist·rr 등)는 그대로 둔다 — 완화가 아니라 정합 수리다.
    if policy.stance_gate_mode == "stable_direction":
        stance_passed = bool(confirmed_bar and stance_direction == direction.value and stance_state.get("transitioning") is not True)
    else:
        stance_passed = bool(
            confirmed_bar and stance_state.get("flipped") is True and stance_state.get("transitioning") is not True and stance_direction == direction.value
        )
    # 시그니처 검증을 진입 조건에서 뺀다(record_only) — 검증 대상을 검증 조건으로 삼으면
    # 표본이 영원히 생기지 않는다. 상태는 아래 signature_status 로 원장에 남는다.
    signature_passed = (
        True
        if policy.signature_gate_mode == "record_only"
        else bool(validated_signature and signature_ci_low_pct is not None and signature_ci_low_pct >= policy.min_signature_ci_low_pct)
    )
    # WO-FCE-NET-EDGE-01. `rr_basis="net"` 이면 하한도 순 기준 하한을 쓴다 — 총 기준
    # 임계(1.5)를 순 기준에 그대로 적용하면 두 축이 한 숫자에 묶여 따로 풀 수 없다.
    minimum_rr = policy.effective_min_net_rr if policy.rr_basis == "net" else policy.min_rr
    gates = {
        "confirmed_flip": stance_passed,
        "evidence": evidence_count >= policy.min_evidence,
        "checklist": checklist_passed >= policy.min_checklist_passed and checklist_total >= policy.min_checklist_total,
        "invalidation_hygiene": invalidation_hygiene,
        # 스톱 경계. **넓히는 쪽은 목표 계획이 이미 처리했고**, 여기서 막는 것은 상한뿐이다 —
        # 조용히 좁히는 대신 거부한다. 경계를 안 쓰면(None) 항상 통과다.
        "stop_bounds": stop_within_bounds(stop_atr_multiple, policy),
        "risk_reward": rr_ratio is not None and rr_ratio >= minimum_rr,
        # 마찰 상한. 비용R = 왕복 비용률 / 스톱거리% — 사이즈와 무관한 산술이다.
        "cost_efficiency": policy.max_entry_cost_r is None or (cost_r is not None and cost_r <= policy.max_entry_cost_r),
        "liquidation_safety": survives_to_invalidation,
        "htf_alignment": not (policy.htf_conflict_blocks and htf_conflict),
        "validated_signature": signature_passed,
        "earnings_clear": earnings_clear,
        "data_fresh": data_fresh,
    }
    rejected = tuple(name for name, passed in gates.items() if not passed)
    # 판정 조건에서 뺀 값들을 **관측치로 원장에 남긴다** — "전환 직후 진입 vs 안정 후 진입",
    # "미검증 시그니처 진입"을 사후 채점할 수 있어야 한다.
    observations = {
        "policy_version": policy.version,
        "stance_gate_mode": policy.stance_gate_mode,
        "signature_gate_mode": policy.signature_gate_mode,
        "stance": stance,
        "stance_direction": stance_direction,
        "flipped": stance_state.get("flipped"),
        "transitioning": stance_state.get("transitioning"),
        "validated_signature_observed": bool(validated_signature),
        "signature_ci_low_pct_observed": signature_ci_low_pct,
        # WO-FCE-NET-EDGE-01. 게이트를 켜지 않아도 **관측은 남긴다** — 임계를 정하려면
        # 먼저 분포를 봐야 하고, 조건에서 뺐다고 관측까지 버리면 그 질문에 답할 수 없다.
        "risk_mode": policy.risk_mode,
        "reward_mode": policy.reward_mode,
        "rr_basis": policy.rr_basis,
        "cost_r_observed": cost_r,
        "stop_atr_multiple_observed": stop_atr_multiple,
        "htf_conflict_observed": bool(htf_conflict),
        "minimum_rr_applied": minimum_rr,
    }
    return EntryDecision(enter=not rejected, gates=gates, rejection_reasons=rejected, observations=observations)


def stop_within_bounds(stop_atr_multiple: float | None, policy: PaperPolicy) -> bool:
    """스톱이 ATR 배수 상한 안에 있는가 (WO-FCE-NET-EDGE-01).

    **하한은 여기서 재지 않는다.** 하한은 거부 사유가 아니라 넓히는 지시이고, 목표 계획이
    이미 넓혀서 넘겨준다. 여기서 또 재면 넓혀진 값이 스스로를 탈락시킨다.

    상한은 반대다 — 구조 무효화가 ATR 의 몇 배나 떨어져 있으면 그 자리는 이 사다리로
    감당할 셋업이 아니다. 현행은 이것을 **조용히 1 ATR 로 좁혀서** 진입했고, 그 축소가
    RR 항등식과 노이즈 손절을 동시에 만들었다. 좁히는 대신 거부한다.
    """
    if policy.max_stop_atr_multiple is None or stop_atr_multiple is None:
        return True
    return stop_atr_multiple <= policy.max_stop_atr_multiple + 1e-9


def reentry_locked(
    *,
    entry_bar_at: datetime,
    direction: Direction,
    last_exit_bar_at: datetime | None,
    last_exit_direction: Direction | None,
    bar_seconds: float,
    policy: PaperPolicy,
) -> str | None:
    """직전 청산 직후 재진입을 막아야 하는가. 막으면 **사유 문자열**을 돌려준다 (C10).

    `off`(기본): 막지 않는다 — 기존 동작.
    `same_bar`: 청산한 그 확정봉 안에서는 다시 들어가지 않는다.
    `bars`: 청산 후 `reentry_lock_bars` 봉이 지나기 전에는 들어가지 않는다.

    **잠금을 넓힐수록 좋아지는 관계가 아니다.** 실측에서 4시간 간격(1봉) 재진입 3건은
    gross 우위가 +1.608R 로 양수였다 — 함께 막으면 성적이 나빠진다. 그래서 채택값은
    `same_bar` 이고, 더 긴 잠금은 표본 33건에서 잡음에 적합된 결과로 본다.
    """
    if policy.reentry_lock_mode == "off" or last_exit_bar_at is None:
        return None
    if policy.reentry_lock_same_direction_only and last_exit_direction is not None and direction != last_exit_direction:
        return None

    if policy.reentry_lock_mode == "same_bar":
        return "reentry_lock:same_bar" if entry_bar_at <= last_exit_bar_at else None

    if policy.reentry_lock_mode == "bars":
        if bar_seconds <= 0:
            return None
        elapsed_bars = (entry_bar_at - last_exit_bar_at).total_seconds() / bar_seconds
        return f"reentry_lock:{policy.reentry_lock_bars}bars" if elapsed_bars <= policy.reentry_lock_bars else None

    return None


def portfolio_cap_block_reason(
    *,
    direction: Direction,
    symbol: str,
    planned_risk_usdt: float,
    open_positions: Sequence[dict[str, Any]],
    policy: PaperPolicy,
) -> str | None:
    """포트폴리오 상한에 걸리는가. 걸리면 **사유 문자열**을 돌려준다 (C11).

    `off`(기본): 막지 않는다 — 기존 동작. `max_open_positions` 만이 유일한 제약이다.

    `open_positions` 는 `{"symbol": str, "direction": str, "planned_risk_usdt": float}` 의
    목록이다. 보유 포지션의 계획 리스크를 모르면(과거 원장) 예산으로 대체한다 — 그 경우
    총리스크 상한은 슬롯 상한과 같아진다.

    **세 축을 독립으로 판정하고 첫 위반에서 멈춘다.** 어느 축이 막았는지 사유에 남겨야
    사후에 축별 효과를 가를 수 있다 — 세 축을 한 사유로 뭉치면 교란된다.
    """
    if policy.portfolio_cap_mode == "off":
        return None

    if policy.max_total_risk_usdt is not None:
        held = sum(float(item.get("planned_risk_usdt") or policy.risk_budget_usdt) for item in open_positions)
        if held + planned_risk_usdt > policy.max_total_risk_usdt + 1e-9:
            return f"portfolio_cap:total_risk>{policy.max_total_risk_usdt:g}"

    if policy.max_same_direction_positions is not None:
        same = sum(1 for item in open_positions if str(item.get("direction") or "") == direction.value)
        if same + 1 > policy.max_same_direction_positions:
            return f"portfolio_cap:same_direction>{policy.max_same_direction_positions}"

    if policy.max_correlation_cluster_positions is not None:
        cluster = policy.correlation_cluster(symbol)
        same_cluster = sum(1 for item in open_positions if policy.correlation_cluster(str(item.get("symbol") or "")) == cluster)
        if same_cluster + 1 > policy.max_correlation_cluster_positions:
            return f"portfolio_cap:cluster[{cluster}]>{policy.max_correlation_cluster_positions}"

    return None


def plan_position_size(
    *,
    entry_price: float,
    invalidation_price: float,
    policy: PaperPolicy,
) -> dict[str, Any]:
    """수량을 정하고 **그 근거를 남긴다** (WO-FCE-RISK-SIZING-01 Phase 1 · C10).

    `fixed_notional`(기본): 기존 동작. `notional = 증거금 × 레버리지`, 스톱 거리와 무관하다.
    `risk_based`: `수량 = 리스크 예산 / |진입가 − 무효화가|`.

    리스크 기준에서는 1R 의 금액가치가 스톱 거리와 무관하게 **리스크 예산 그 자체**가 된다.
    그래서 R 합계의 부호가 곧 금액 합계의 부호가 된다 — 지금은 둘이 어긋나 있다.

    **R 회계는 건드리지 않는다.** 수량이 s 배가 되면 손익도 비용도 계획 리스크도 같이 s 배가
    되므로 R = 손익 / 계획리스크 는 불변이다. 반사실 산출에서 실측으로 확인했다(불변 −2.747R).

    상한·하한에 걸리면 실제 리스크가 예산과 달라진다. 그 사실을 숨기지 않고 기록한다.
    """
    fallback_notional = policy.margin_usdt * policy.leverage
    stop_distance = abs(entry_price - invalidation_price)

    if policy.sizing_mode != "risk_based" or stop_distance <= 0.0 or entry_price <= 0.0:
        # 무효화가가 없거나 진입가와 같으면 리스크를 나눌 수 없다. 기존 경로로 되돌린다.
        reason = "mode:fixed_notional" if policy.sizing_mode != "risk_based" else "fallback:no_stop_distance"
        return {
            "mode": "fixed_notional",
            "quantity": fallback_notional / entry_price,
            "notional_usdt": fallback_notional,
            "margin_usdt": policy.margin_usdt,
            "stop_distance": stop_distance,
            "stop_distance_pct": (stop_distance / entry_price * 100.0) if entry_price > 0 else None,
            "risk_budget_usdt": None,
            "planned_risk_usdt": stop_distance * (fallback_notional / entry_price),
            "constraint": reason,
        }

    quantity = policy.risk_budget_usdt / stop_distance
    notional = quantity * entry_price
    constraint = "none"

    # 스톱 거리가 극히 작으면 수량이 발산한다. 상한이 없으면 한 건이 계좌를 지운다.
    if notional > policy.max_notional_usdt:
        quantity = policy.max_notional_usdt / entry_price
        notional = policy.max_notional_usdt
        constraint = "max_notional"
    elif notional < policy.min_notional_usdt:
        # 거래소 최소 주문 미만. 리스크가 예산을 **넘게** 되므로 그 사실을 남긴다.
        quantity = policy.min_notional_usdt / entry_price
        notional = policy.min_notional_usdt
        constraint = "min_notional"

    return {
        "mode": "risk_based",
        "quantity": quantity,
        "notional_usdt": notional,
        "margin_usdt": notional / policy.leverage if policy.leverage > 0 else notional,
        "stop_distance": stop_distance,
        "stop_distance_pct": stop_distance / entry_price * 100.0,
        "risk_budget_usdt": policy.risk_budget_usdt,
        # 상한에 걸리면 실제 리스크가 예산과 다르다. 예산이 아니라 **실제**를 적는다.
        "planned_risk_usdt": stop_distance * quantity,
        "constraint": constraint,
    }


def open_trade(
    *,
    trade_id: UUID,
    symbol: str,
    timeframe: str,
    asset_class: str,
    direction: Direction,
    bar: MarketCandle,
    invalidation_price: float,
    take_profit_price: float,
    evidence: dict[str, Any],
    checklist: dict[str, Any],
    stance_snapshot: dict[str, Any],
    signature_snapshot: dict[str, Any],
    policy: PaperPolicy,
    take_profit_2_price: float | None = None,
    entry_atr: float | None = None,
    target_plan: dict[str, Any] | None = None,
) -> PaperTrade:
    sizing = plan_position_size(
        entry_price=bar.close,
        invalidation_price=invalidation_price,
        policy=policy,
    )
    notional = sizing["notional_usdt"]
    quantity = sizing["quantity"]
    entry_cost = notional * policy.execution_cost_rate
    return PaperTrade(
        id=trade_id,
        symbol=symbol.upper(),
        timeframe=timeframe,
        asset_class=asset_class,
        direction=direction,
        entry_bar_at=bar.timestamp,
        entry_at=bar.timestamp,
        entry_price=bar.close,
        margin_usdt=sizing["margin_usdt"],
        leverage=policy.leverage,
        quantity=quantity,
        remaining_quantity=quantity,
        invalidation_price=invalidation_price,
        take_profit_price=take_profit_price,
        take_profit_2_price=take_profit_2_price,
        entry_atr=entry_atr,
        target_plan={**(target_plan or {}), "sizing": sizing},
        stop_price=invalidation_price,
        entry_evidence=evidence,
        checklist=checklist,
        stance_snapshot=stance_snapshot,
        signature_snapshot=signature_snapshot,
        costs_usdt=entry_cost,
        net_pnl_usdt=-entry_cost,
        net_return_pct=(-entry_cost / sizing["margin_usdt"]) * 100.0,
        judgment_id=f"paper:{trade_id}:entry",
        created_at=bar.timestamp,
        updated_at=bar.timestamp,
    )


def evaluate_exit(
    trade: PaperTrade,
    *,
    bar: MarketCandle,
    stance_state: dict[str, Any],
    take_profit_pressure: str | None,
    prior_high_pressure_streak: int,
    policy: PaperPolicy,
) -> ExitDecision:
    next_holding_bars = trade.holding_bars + 1
    stop_fill = stop_fill_price(trade, bar=bar, policy=policy)
    if stop_fill is not None:
        reason: ExitReason = "breakeven_stop" if trade.partial_exit_at else "invalidation_breach"
        return ExitDecision("close", reason, 0, stop_fill)

    if trade.partial_exit_at is None and _take_profit_touched(trade, bar):
        return ExitDecision("partial", "take_profit_1", 0, trade.take_profit_price)

    if trade.partial_exit_at is not None and _take_profit_2_touched(trade, bar):
        return ExitDecision("close", "take_profit_2", 0, trade.take_profit_2_price)

    if _opposite_confirmed_flip(trade, stance_state):
        return ExitDecision("close", "opposite_stance_flip", 0)

    # Take-profit pressure protects gains on the remainder; it is not a pre-TP
    # stop and must never close a position that has not realized TP1.
    high_streak = prior_high_pressure_streak + 1 if trade.partial_exit_at is not None and take_profit_pressure == "high" else 0
    if trade.partial_exit_at is not None and high_streak >= policy.take_profit_pressure_bars:
        return ExitDecision("close", "take_profit_pressure", high_streak)
    if next_holding_bars >= policy.max_holding_bars:
        if _stance_supports_trade(trade, stance_state):
            return ExitDecision("hold", "none", high_streak)
        return ExitDecision("close", "time_decay", high_streak)
    return ExitDecision("hold", "none", high_streak)


def apply_exit_decision(
    trade: PaperTrade,
    *,
    decision: ExitDecision,
    bar: MarketCandle,
    policy: PaperPolicy,
) -> PaperTrade:
    holding_bars = trade.holding_bars + 1
    event_at = max(bar.timestamp, trade.entry_at)
    if decision.action == "hold":
        return trade.model_copy(update={"holding_bars": holding_bars, "updated_at": event_at})

    execution_price = decision.execution_price or bar.close
    exit_quantity = trade.remaining_quantity * (0.5 if decision.action == "partial" else 1.0)
    gross_increment = _gross_pnl(trade.direction, trade.entry_price, execution_price, exit_quantity)
    exit_cost = execution_price * exit_quantity * policy.execution_cost_rate
    gross = trade.gross_pnl_usdt + gross_increment
    costs = trade.costs_usdt + exit_cost
    net = gross - costs
    common = {
        "gross_pnl_usdt": gross,
        "costs_usdt": costs,
        "net_pnl_usdt": net,
        "net_return_pct": (net / trade.margin_usdt) * 100.0,
        "holding_bars": holding_bars,
        "updated_at": event_at,
    }
    if decision.action == "partial":
        return trade.model_copy(
            update={
                **common,
                "remaining_quantity": trade.remaining_quantity - exit_quantity,
                "partial_exit_at": event_at,
                "partial_exit_price": execution_price,
                "partial_exit_quantity": exit_quantity,
                "stop_price": trade.entry_price,
            }
        )
    return trade.model_copy(
        update={
            **common,
            "status": "closed",
            "remaining_quantity": 0.0,
            "exit_bar_at": bar.timestamp,
            "exit_at": event_at,
            "exit_price": execution_price,
            "exit_reason": decision.reason,
        }
    )


def stop_fill_price(trade: PaperTrade, *, bar: MarketCandle, policy: PaperPolicy) -> float | None:
    """이 봉에서 손절이 체결되는가, 그렇다면 **얼마에** (WO-FCE-NET-EDGE-01).

    `close`(기본): 봉이 닫힐 때까지 기다렸다 종가에 체결한다 — 기존 동작. 봉 중간에
    무효화가를 관통했다가 되돌아온 봉은 손절되지 않는다.

    `intrabar`: 실제 스톱 주문이 하는 일이다. **무효화 임계는 한 줄도 완화되지 않는다 —
    같은 가격에서 더 일찍 체결될 뿐이다.** 익절은 이미 봉 중간 터치이므로 이 모드에서
    비로소 두 방향의 체결 규칙이 같아진다(실측: 초과 손실의 82% 가 이 비대칭에서 왔다).

    갭은 따로 다룬다. 봉이 **이미 무효화가를 넘어서 열렸으면** 스톱 가격에 체결될 수 없다 —
    시가가 첫 체결 가능 가격이다. 이것을 무효화가로 적으면 갭 손실이 원장에서 사라진다
    (실측 SPCXUSDT 1건이 8건 초과분의 56%). 시가는 봉 시작에 알 수 있으므로 룩어헤드가
    아니다.
    """
    if policy.stop_fill_mode != "intrabar":
        return bar.close if _stop_breached(trade, bar.close) else None
    if trade.direction == Direction.long:
        if bar.open <= trade.stop_price:
            return bar.open
        return trade.stop_price if bar.low <= trade.stop_price else None
    if bar.open >= trade.stop_price:
        return bar.open
    return trade.stop_price if bar.high >= trade.stop_price else None


def _stop_breached(trade: PaperTrade, close: float) -> bool:
    if trade.direction == Direction.long:
        return close <= trade.stop_price
    return close >= trade.stop_price


def _take_profit_touched(trade: PaperTrade, bar: MarketCandle) -> bool:
    if trade.direction == Direction.long:
        return bar.high >= trade.take_profit_price
    return bar.low <= trade.take_profit_price


def _take_profit_2_touched(trade: PaperTrade, bar: MarketCandle) -> bool:
    target = trade.take_profit_2_price
    if target is None:
        return False
    if trade.direction == Direction.long:
        return bar.high >= target
    return bar.low <= target


def _opposite_confirmed_flip(trade: PaperTrade, stance_state: dict[str, Any]) -> bool:
    if stance_state.get("flipped") is not True or stance_state.get("transitioning") is True:
        return False
    stance = str(stance_state.get("stance") or "")
    return (trade.direction == Direction.long and stance in {"short", "short_leaning"}) or (
        trade.direction == Direction.short and stance in {"long", "long_leaning"}
    )


def _stance_supports_trade(trade: PaperTrade, stance_state: dict[str, Any]) -> bool:
    if stance_state.get("transitioning") is True:
        return False
    stance = str(stance_state.get("stance") or "")
    if trade.direction == Direction.long:
        return stance in {"long", "long_leaning"}
    return stance in {"short", "short_leaning"}


def _gross_pnl(direction: Direction, entry: float, exit_price: float, quantity: float) -> float:
    sign = 1.0 if direction == Direction.long else -1.0
    return (exit_price - entry) * quantity * sign
