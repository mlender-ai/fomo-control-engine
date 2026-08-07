"""WO-FCE-SAMPLE-RATE-01 Phase 3 — 필요 표본 재산정.

## 무엇을 묻는가

`N ≥ 30` 은 통계 관행의 경험칙이지 **이 검증 설계에 최적화된 값이 아니다.** 여기서
반드시 구분해야 하는 것이 있다:

- **기준을 낮추는 것** = 같은 방법으로 더 적은 증거를 받아들이는 것 → 금지
- **더 효율적인 추정량을 쓰는 것** = 같은 확신을 더 적은 표본으로 얻는 것 → 정당하다

이 스크립트는 후자만 다룬다. 다섯 방법 각각에 대해 **같은 검정력(기본 80%)** 을 얻는 데
필요한 표본 수를 몬테카를로로 재산정한다. 확신의 수준은 고정하고 표본만 비교한다.

## 결정론

시드를 고정한다. 같은 입력이면 같은 표를 낸다 — 통계 방법 비교가 실행할 때마다 달라지면
그 비교를 근거로 쓸 수 없다.

## 부트스트랩 CI 의 해석적 대역

현재 방법(M0)은 두 부트스트랩 95% CI 의 **비겹침**을 요구한다. 비율의 백분위 부트스트랩
CI 는 `p̂ ± 1.96·√(p̂(1-p̂)/n)` 로 수렴하므로 시뮬레이션에서는 그 형태를 쓴다. 매 시행마다
1,000회 리샘플을 돌리면 표를 못 뽑기 때문이며, **판정 규칙 자체는 동일하다.**

사용:
    python3 scripts/required_sample_analysis.py
    python3 scripts/required_sample_analysis.py --engine 0.60 --benchmark 0.45 --power 0.8
"""

from __future__ import annotations

import argparse
import math
import random

Z95 = 1.959963984540054
SEED = 20260807


def _se(p: float, n: int) -> float:
    return math.sqrt(max(p * (1 - p), 1e-12) / n)


def _draw(rng: random.Random, p: float, n: int) -> int:
    return sum(1 for _ in range(n) if rng.random() < p)


# ── 판정 방법 ───────────────────────────────────────────────────────────


def m0_ci_non_overlap(rng: random.Random, p_engine: float, p_bench: float, n: int) -> bool:
    """현재 방법 — 두 95% CI 가 겹치지 않아야 우위로 인정한다."""
    pe, pb = _draw(rng, p_engine, n) / n, _draw(rng, p_bench, n) / n
    return (pe - Z95 * _se(pe, n)) > (pb + Z95 * _se(pb, n))


def m1_two_proportion(rng: random.Random, p_engine: float, p_bench: float, n: int, *, alpha: float = 0.05) -> bool:
    """독립 2표본 비율 검정(단측). 확신 수준을 **명시적으로** 고른다."""
    pe, pb = _draw(rng, p_engine, n) / n, _draw(rng, p_bench, n) / n
    pooled = (pe + pb) / 2
    se = math.sqrt(max(2 * pooled * (1 - pooled) / n, 1e-12))
    z = (pe - pb) / se
    critical = {0.05: 1.6448536269514722, 0.025: 1.959963984540054, 0.01: 2.3263478740408408}[alpha]
    return z > critical


def m2_paired_mcnemar(rng: random.Random, p_engine: float, p_bench: float, n: int, *, rho: float, alpha: float = 0.05) -> bool:
    """페어드(McNemar) — 같은 날·같은 조건에서 대조하면 공통 분산이 상쇄된다.

    `rho` 는 공통 시장 요인의 비중이다. 커질수록 "둘 다 이기거나 둘 다 지는" 쌍이 늘고
    불일치쌍이 **효과 방향으로만** 남아 필요 표본이 줄어든다. 0이면 페어링 이득이 없다 —
    이 사실이 "페어링이 실제로 가능한가"를 먼저 실측해야 하는 이유다.

    커플링은 **각 팔의 주변 승률을 보존해야 한다.** 공통 요인 구간에서는 같은 난수 하나로
    두 팔을 판정한다(comonotonic): `u < p` 이면 승. 이러면 P(엔진 승)=p_engine,
    P(벤치 승)=p_bench 가 그대로 유지되면서 상관만 올라간다. 벤치 결과를 엔진 결과로
    복사하는 방식은 벤치의 주변 승률을 엔진 쪽으로 끌어올려 **참 효과 자체를 지운다.**
    """
    discordant_eb = 0  # 엔진 승 · 벤치 패
    discordant_be = 0
    for _ in range(n):
        if rng.random() < rho:
            shared = rng.random()
            engine_win, bench_win = shared < p_engine, shared < p_bench
        else:
            engine_win, bench_win = rng.random() < p_engine, rng.random() < p_bench
        if engine_win and not bench_win:
            discordant_eb += 1
        elif bench_win and not engine_win:
            discordant_be += 1
    total = discordant_eb + discordant_be
    if total == 0:
        return False
    z = (discordant_eb - discordant_be) / math.sqrt(total)
    return z > (1.6448536269514722 if alpha == 0.05 else 1.959963984540054)


def m3_sequential(rng: random.Random, p_engine: float, p_bench: float, n: int, *, looks: int = 4, alpha: float = 0.05) -> bool:
    """순차 검정 — 증거가 충분해지면 조기 종료.

    **다중 검정 보정 필수.** 보정 없이 여러 번 들여다보면 조기 종료가 위양성 공장이 된다.
    Pocock 형 상수 보정(같은 임계를 모든 관찰 시점에 적용)을 쓴다 — 보수적이고 단순하다.
    """
    # Pocock 경계: 관찰 4회, 전체 α=0.05 단측 → 각 시점 임계 z ≈ 2.36.
    critical = {2: 2.178, 3: 2.289, 4: 2.361, 5: 2.413}[looks]
    engine_wins = bench_wins = 0
    step = max(1, n // looks)
    for look in range(1, looks + 1):
        size = step if look < looks else n - step * (looks - 1)
        engine_wins += _draw(rng, p_engine, size)
        bench_wins += _draw(rng, p_bench, size)
        seen = step * (look - 1) + size
        pe, pb = engine_wins / seen, bench_wins / seen
        pooled = (pe + pb) / 2
        se = math.sqrt(max(2 * pooled * (1 - pooled) / seen, 1e-12))
        if se > 0 and (pe - pb) / se > critical:
            return True
    return False


def m4_effect_size(rng: random.Random, p_engine: float, p_bench: float, n: int, *, min_effect: float = 0.10) -> bool:
    """효과 크기 기준 — "이겼나"가 아니라 "얼마나 이겼나".

    관측 차이가 최소 효과 이상이고 **차이의 CI 하한이 0을 넘어야** 한다. 작은 효과는
    애초에 자동매매 근거로 부족하므로, 이 기준은 느슨한 것이 아니라 **다른 질문**이다.
    """
    pe, pb = _draw(rng, p_engine, n) / n, _draw(rng, p_bench, n) / n
    delta = pe - pb
    se = math.sqrt(max(pe * (1 - pe) / n + pb * (1 - pb) / n, 1e-12))
    return delta >= min_effect and (delta - Z95 * se) > 0


# ── 검정력 탐색 ─────────────────────────────────────────────────────────


def power_at(method, p_engine: float, p_bench: float, n: int, *, trials: int, seed: int, **kwargs) -> float:
    rng = random.Random(seed + n)
    hits = sum(1 for _ in range(trials) if method(rng, p_engine, p_bench, n, **kwargs))
    return hits / trials


def required_n(method, p_engine: float, p_bench: float, *, power: float, trials: int, seed: int, cap: int = 2000, **kwargs) -> int | None:
    """검정력 목표를 만족하는 최소 N. 상한까지 못 미치면 None (모른다고 말한다)."""
    low, high = 5, cap
    if power_at(method, p_engine, p_bench, high, trials=trials, seed=seed, **kwargs) < power:
        return None
    while low < high:
        mid = (low + high) // 2
        if power_at(method, p_engine, p_bench, mid, trials=trials, seed=seed, **kwargs) >= power:
            high = mid
        else:
            low = mid + 1
    return low


def false_positive_rate(method, p_null: float, n: int, *, trials: int, seed: int, **kwargs) -> float:
    """효과가 **없을 때** 우위로 판정하는 비율. 방법을 바꿔도 이 값이 커지면 안 된다."""
    return power_at(method, p_null, p_null, n, trials=trials, seed=seed, **kwargs)


def main() -> None:
    parser = argparse.ArgumentParser(description="검증 필요 표본 재산정 (WO-FCE-SAMPLE-RATE-01 Phase 3).")
    parser.add_argument("--engine", type=float, default=0.60, help="엔진 참 승률")
    parser.add_argument("--benchmark", type=float, default=0.45, help="벤치마크 참 승률")
    parser.add_argument("--power", type=float, default=0.80, help="목표 검정력")
    parser.add_argument("--trials", type=int, default=3000, help="시행 수 (결정론 시드 고정)")
    args = parser.parse_args()

    methods = [
        ("M0 현재 — 두 95% CI 비겹침", m0_ci_non_overlap, {}),
        ("M1 독립 2표본 비율검정 (α=0.05 단측)", m1_two_proportion, {}),
        ("M2 페어드 McNemar (ρ=0.0 · 대조군)", m2_paired_mcnemar, {"rho": 0.0}),
        ("M2 페어드 McNemar (ρ=0.3)", m2_paired_mcnemar, {"rho": 0.3}),
        ("M2 페어드 McNemar (ρ=0.5)", m2_paired_mcnemar, {"rho": 0.5}),
        ("M2 페어드 McNemar (ρ=0.7)", m2_paired_mcnemar, {"rho": 0.7}),
        ("M3 순차검정 (4회 관찰 · Pocock 보정)", m3_sequential, {}),
        ("M4 효과크기 (Δ≥10%p + CI 하한>0)", m4_effect_size, {}),
    ]

    print(f"# 필요 표본 재산정 — 엔진 {args.engine:.0%} vs 벤치마크 {args.benchmark:.0%} · 검정력 {args.power:.0%}")
    print(f"# 시행 {args.trials} · 시드 {SEED} (결정론)\n")
    print("| 방법 | 필요 표본 N | 현재 대비 | 효과 없을 때 오판율 |")
    print("|---|---|---|---|")
    baseline: int | None = None
    for label, method, kwargs in methods:
        n = required_n(method, args.engine, args.benchmark, power=args.power, trials=args.trials, seed=SEED, **kwargs)
        if baseline is None:
            baseline = n
        ratio = f"{n / baseline:.2f}배" if n and baseline else "—"
        fpr = false_positive_rate(method, args.benchmark, n or 100, trials=args.trials, seed=SEED, **kwargs)
        print(f"| {label} | {n if n else '2000 초과'} | {ratio} | {fpr:.1%} |")
    print("\n> 오판율은 **효과가 전혀 없을 때** 우위로 판정하는 비율이다. 필요 표본이 줄어도")
    print("> 이 값이 커지면 그것은 효율이 아니라 기준 완화다.")


if __name__ == "__main__":
    main()
