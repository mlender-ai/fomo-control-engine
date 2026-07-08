"""증거 중복 상관 — 컨플루언스 이중 가점 방지 (WO-FCE-36 §6).

시그니처 쌍의 동시 발생률(같은 캔들 ±2)을 측정해 상관 > 임계 쌍을
overlap_group으로 묶는다. 브리핑은 같은 그룹의 증거를 최강 1개만 가중 반영한다.
표본이 부족한 구간을 위해 도메인 사전 그룹(스윕↔Spring 등)을 병기한다.
"""

from __future__ import annotations

from typing import Any

BAR_TOLERANCE = 2
MIN_PAIR_SAMPLE = 5

# 같은 가격 행동의 다른 이름 — 측정 표본이 부족해도 적용하는 도메인 사전 그룹.
PRIOR_OVERLAP_FAMILIES: list[dict[str, Any]] = [
    {
        "group_id": "prior:liquidity_sweep~wyckoff_event:long",
        "families": [["liquidity", "sweep", "long"], ["wyckoff", "event", "long"]],
        "source": "prior",
        "note": "저점 스윕과 Spring류 이벤트는 같은 유동성 흡수를 다른 엔진이 명명한 것",
    },
    {
        "group_id": "prior:liquidity_sweep~wyckoff_event:short",
        "families": [["liquidity", "sweep", "short"], ["wyckoff", "event", "short"]],
        "source": "prior",
        "note": "고점 스윕과 UTAD류 이벤트는 같은 유동성 흡수를 다른 엔진이 명명한 것",
    },
]


def event_family(signature: dict[str, Any]) -> tuple[str, str, str]:
    """시그니처를 (engine, event_family, direction) 패밀리로 축약한다."""
    engine = str(signature.get("engine") or "-")
    event = str(signature.get("event_type") or "-")
    direction = str(signature.get("direction") or "neutral")
    if engine == "liquidity":
        family = "sweep" if "sweep" in event else event
    elif engine == "wyckoff":
        family = "event"
    elif engine == "harmonic":
        family = "prz"
    elif engine == "levels":
        family = "level"
    else:
        family = event
    return (engine, family, direction)


def compute_overlap_groups(
    cases: list[dict[str, Any]],
    *,
    bar_tolerance: int = BAR_TOLERANCE,
    threshold: float = 0.7,
    min_pair_sample: int = MIN_PAIR_SAMPLE,
) -> list[dict[str, Any]]:
    """리플레이 케이스에서 시그니처 패밀리 쌍의 동시 발생률을 측정한다."""
    occurrences: dict[tuple[str, str, str], set[int]] = {}
    for case in cases:
        signature = case.get("signature") if isinstance(case.get("signature"), dict) else {}
        index = case.get("confirmation_index")
        if not isinstance(index, int):
            continue
        occurrences.setdefault(event_family(signature), set()).add(index)

    families = sorted(occurrences.keys())
    pairs: list[dict[str, Any]] = []
    adjacency: dict[tuple[str, str, str], set[tuple[str, str, str]]] = {family: set() for family in families}
    for i, family_a in enumerate(families):
        for family_b in families[i + 1 :]:
            if family_a[2] != family_b[2]:  # 방향이 다르면 동근원이 아니다
                continue
            indices_a = occurrences[family_a]
            indices_b = occurrences[family_b]
            base = min(len(indices_a), len(indices_b))
            if base < min_pair_sample:
                continue
            smaller, larger = (indices_a, indices_b) if len(indices_a) <= len(indices_b) else (indices_b, indices_a)
            co_occurring = sum(
                1
                for index in smaller
                if any(abs(index - other) <= bar_tolerance for other in larger)
            )
            overlap = round(co_occurring / base, 3)
            pairs.append(
                {
                    "family_a": list(family_a),
                    "family_b": list(family_b),
                    "overlap": overlap,
                    "base_sample": base,
                    "linked": overlap > threshold,
                }
            )
            if overlap > threshold:
                adjacency[family_a].add(family_b)
                adjacency[family_b].add(family_a)

    groups: list[dict[str, Any]] = []
    visited: set[tuple[str, str, str]] = set()
    for family in families:
        if family in visited or not adjacency[family]:
            continue
        component: set[tuple[str, str, str]] = set()
        stack = [family]
        while stack:
            node = stack.pop()
            if node in component:
                continue
            component.add(node)
            stack.extend(adjacency[node] - component)
        visited.update(component)
        ordered_component = sorted(component)
        groups.append(
            {
                "group_id": "measured:" + "~".join(":".join(item) for item in ordered_component),
                "families": [list(item) for item in ordered_component],
                "source": "measured",
                "pairs": [
                    pair
                    for pair in pairs
                    if pair["linked"] and tuple(pair["family_a"]) in component and tuple(pair["family_b"]) in component
                ],
            }
        )
    return groups


def overlap_groups_payload(cases: list[dict[str, Any]], *, threshold: float = 0.7) -> list[dict[str, Any]]:
    """측정 그룹 + 사전 그룹 병합 (측정이 이미 커버하는 사전 그룹은 생략)."""
    measured = compute_overlap_groups(cases, threshold=threshold)
    covered = {frozenset(tuple(family) for family in group["families"]) for group in measured}
    merged = list(measured)
    for prior in PRIOR_OVERLAP_FAMILIES:
        prior_set = frozenset(tuple(family) for family in prior["families"])
        if not any(prior_set <= existing for existing in covered):
            merged.append(prior)
    return merged
