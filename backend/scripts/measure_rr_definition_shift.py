"""RR 정의 변경(TP1 단독 → 분할 청산 가중)의 게이트 영향 측정 (WO-FCE-PNF-TARGET-01 작업 1·5).

**이것은 시장 백테스트가 아니라 결정론적 민감도 그리드다.** 실히스토리 DB가 없는 환경에서
시장 성과를 주장하지 않기 위해, 입력 (TP1%, TP2%, risk%) 격자에 대해 두 RR 정의가 각각
`min_rr` 게이트를 어떻게 통과/거부하는지만 계산한다. 여기서 나오는 수치는 "게이트 산술의
변화"이지 "수익 개선"이 아니다.

실행: PYTHONPATH=. python3 scripts/measure_rr_definition_shift.py
"""

from __future__ import annotations

import itertools

from app.stock_paper.parameters import load_stock_parameters
from app.stock_paper.risk_reward import risk_reward

# 구조 레벨 기반 목표·손절폭의 현실적 범위(일봉 주식). 실제 관측 분포가 아니라 격자다.
TP1_RANGE = [1.0, 2.0, 3.0, 4.0, 5.0, 7.0, 10.0]
TP2_MULTIPLIER = [1.5, 2.0, 3.0]
RISK_RANGE = [2.0, 3.0, 4.0, 5.0, 7.0]


def main() -> None:
    min_rr = load_stock_parameters().min_rr
    rows = []
    for tp1, multiplier, risk in itertools.product(TP1_RANGE, TP2_MULTIPLIER, RISK_RANGE):
        targets = [{"distance_pct": tp1}, {"distance_pct": tp1 * multiplier}]
        payload = risk_reward(targets, risk_pct=risk)
        rows.append(
            {
                "tp1": tp1,
                "tp2": round(tp1 * multiplier, 2),
                "risk": risk,
                "rr_first": payload["rr_first_target"],
                "rr_weighted": payload["rr_weighted"],
                "pass_first": payload["rr_first_target"] >= min_rr,
                "pass_weighted": payload["rr_weighted"] >= min_rr,
            }
        )

    total = len(rows)
    pass_first = sum(1 for row in rows if row["pass_first"])
    pass_weighted = sum(1 for row in rows if row["pass_weighted"])
    newly_passing = [row for row in rows if row["pass_weighted"] and not row["pass_first"]]
    newly_rejected = [row for row in rows if row["pass_first"] and not row["pass_weighted"]]

    print("=" * 78)
    print("RR 정의 변경의 게이트 영향 — 결정론적 민감도 그리드 (시장 백테스트 아님)")
    print("=" * 78)
    print(f"임계 min_rr = {min_rr} (불변 — C1)")
    print(f"격자 조합 수: {total}\n")
    print(f"기존 정의(TP1 단독) 통과:   {pass_first:4d} / {total}  ({pass_first / total * 100:5.1f}%)")
    print(f"가중 정의(분할 청산) 통과:  {pass_weighted:4d} / {total}  ({pass_weighted / total * 100:5.1f}%)")
    print(f"\n새로 통과(기아 해소):       {len(newly_passing):4d}")
    print(f"새로 거부(보수화):          {len(newly_rejected):4d}  ← 0이어야 정합화(완화 아님)")

    print("\n--- 새로 통과하는 조합 표본 (기존 정의에서 목표 산출 방식이 스스로를 거르던 경우) ---")
    print(f"{'TP1%':>6} {'TP2%':>7} {'risk%':>6} {'RR(기존)':>10} {'RR(가중)':>10}")
    for row in newly_passing[:12]:
        print(f"{row['tp1']:6.1f} {row['tp2']:7.2f} {row['risk']:6.1f} {row['rr_first']:10.2f} {row['rr_weighted']:10.2f}")

    print("\n--- WO 실측 사례 대조: NVDA RR 0.53 / 임계 1.5 ---")
    # 실측된 RR 0.53은 TP1 단독 기준이다. 같은 손절폭에서 TP2가 존재할 때의 가중 RR을 본다.
    nvda_risk = 5.0
    nvda_tp1 = 0.53 * nvda_risk  # 역산: TP1 = RR × risk
    for multiplier in TP2_MULTIPLIER:
        payload = risk_reward([{"distance_pct": nvda_tp1}, {"distance_pct": nvda_tp1 * multiplier}], risk_pct=nvda_risk)
        verdict = "통과" if payload["rr_weighted"] >= min_rr else "여전히 거부"
        print(f"TP2={multiplier:.1f}×TP1 → RR(기존) {payload['rr_first_target']:.2f} · RR(가중) {payload['rr_weighted']:.2f} → {verdict}")
    print("\n해석 (정직하게):")
    print("  1. '새로 거부 0'은 가중평균이 TP1 이상이라 **산술적으로 당연**하다. 따라서 이 변경은")
    print("     임계(min_rr=1.5)를 건드리지 않지만 **통과율은 올라간다** — 실질적으로 진입이 늘어난다.")
    print("  2. 정당화 근거는 '기준을 낮췄다'가 아니라 '분자가 틀렸었다'이다. 실제 운용은 분할")
    print("     청산인데 RR은 TP1만 셌으므로 보상이 체계적으로 과소 집계됐다.")
    print("  3. 그러나 목표가 실제로 멀지 않으면 여전히 거부된다(NVDA 사례: 3배 TP2에도 1.02 < 1.5).")
    print("     즉 무차별 통과가 아니다.")
    print("  4. **이 변경이 성과를 개선하는지는 이 표로 알 수 없다.** 청산 표본이 0이므로")
    print("     승률·실현 R 판정은 불가하며, 판정은 표본이 쌓인 뒤에만 가능하다.")


if __name__ == "__main__":
    main()
