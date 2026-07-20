"""시그니처 통계 엄밀화 (WO-FCE-36).

부트스트랩 신뢰구간 · OOS 시간 분할 · 롤링 워크포워드 · 표기 표준.
모든 계산은 결정론이다 — 부트스트랩 시드는 승패 시퀀스 자체에서 파생한다.
"""

from __future__ import annotations

import random
import zlib
from datetime import datetime, timedelta, timezone
from typing import Any

DISCLAIMER_NET = "과거 통계 · 미래 보장 아님 · 수수료·슬리피지 반영(net)"


def bootstrap_win_ci(
    wins: list[bool],
    *,
    iterations: int = 1000,
    confidence: float = 0.95,
) -> tuple[float, float] | None:
    """승패 시퀀스의 부트스트랩(리샘플) 95% 신뢰구간. 결정론(시퀀스 기반 시드)."""
    n = len(wins)
    if n == 0:
        return None
    seed = zlib.crc32("".join("1" if win else "0" for win in wins).encode())
    rng = random.Random(seed)
    rates = sorted(sum(1 for _ in range(n) if wins[rng.randrange(n)]) / n for _ in range(max(100, iterations)))
    total = len(rates)
    low_index = max(0, int((1 - confidence) / 2 * total))
    high_index = min(total - 1, int((1 + confidence) / 2 * total) - 1)
    return (round(rates[low_index] * 100, 1), round(rates[high_index] * 100, 1))


def bootstrap_ci_from_counts(
    correct: int,
    tested: int,
    *,
    iterations: int = 1000,
    confidence: float = 0.95,
) -> tuple[float, float] | None:
    """(correct, tested) 카운트에서 승패 벡터를 복원해 CI를 산출한다."""
    if tested <= 0:
        return None
    correct = max(0, min(tested, correct))
    wins = [True] * correct + [False] * (tested - correct)
    return bootstrap_win_ci(wins, iterations=iterations, confidence=confidence)


def oos_split(
    cases: list[dict[str, Any]],
    *,
    validation_ratio: float = 0.30,
    unstable_gap_pct: float = 15.0,
    min_slice_sample: int = 5,
) -> dict[str, Any] | None:
    """시간순 학습(과거)/검증(최근) 분할. 두 구간 승률 괴리 > 임계면 unstable."""
    ordered = _sorted_cases(cases)
    n = len(ordered)
    if n < min_slice_sample * 2:
        return None
    split = max(min_slice_sample, int(round(n * (1 - validation_ratio))))
    split = min(split, n - min_slice_sample)
    train, validation = ordered[:split], ordered[split:]
    train_rate = _win_rate(train)
    validation_rate = _win_rate(validation)
    if train_rate is None or validation_rate is None:
        return None
    gap = round(abs(train_rate - validation_rate), 1)
    return {
        "train": {"sample_size": len(train), "win_1r_pct": train_rate},
        "validation": {"sample_size": len(validation), "win_1r_pct": validation_rate},
        "gap_pct": gap,
        "unstable": gap > unstable_gap_pct,
        "policy": f"학습 {round((1 - validation_ratio) * 100)}% / 검증 {round(validation_ratio * 100)}% 시간 분할 · 괴리 > {unstable_gap_pct}%p면 unstable",
    }


def walk_forward_curve(
    cases: list[dict[str, Any]],
    *,
    window_days: int = 180,
    step_days: int = 60,
    min_window_sample: int = 5,
) -> list[dict[str, Any]]:
    """롤링 워크포워드 구간별 승률 시계열 — 성능 부패 곡선(WO-37 입력)."""
    ordered = _sorted_cases(cases)
    stamps = [_case_dt(case) for case in ordered]
    stamped = [(stamp, case) for stamp, case in zip(stamps, ordered) if stamp is not None]
    if not stamped:
        return []
    start = stamped[0][0]
    end = stamped[-1][0]
    curve: list[dict[str, Any]] = []
    cursor = start
    while cursor <= end:
        window_end = cursor + timedelta(days=window_days)
        window_cases = [case for stamp, case in stamped if cursor <= stamp < window_end]
        rate = _win_rate(window_cases)
        curve.append(
            {
                "window_start": cursor.isoformat(),
                "window_end": window_end.isoformat(),
                "sample_size": len(window_cases),
                "win_1r_pct": rate,
                "sample_sufficient": len(window_cases) >= min_window_sample,
            }
        )
        cursor = cursor + timedelta(days=step_days)
    return curve


def enrich_signature_stat(
    stat: dict[str, Any],
    cases: list[dict[str, Any]],
    *,
    iterations: int = 1000,
    confidence: float = 0.95,
    validation_ratio: float = 0.30,
    unstable_gap_pct: float = 15.0,
    walk_forward_window_days: int = 180,
    walk_forward_step_days: int = 60,
) -> dict[str, Any]:
    """시그니처 집계에 CI·OOS·워크포워드·레짐 분해를 부착한다."""
    ordered = _sorted_cases(cases)
    wins = [_case_win(case) for case in ordered]
    ci = bootstrap_win_ci(wins, iterations=iterations, confidence=confidence)
    oos = oos_split(ordered, validation_ratio=validation_ratio, unstable_gap_pct=unstable_gap_pct)
    curve = walk_forward_curve(ordered, window_days=walk_forward_window_days, step_days=walk_forward_step_days)
    regimes: dict[str, dict[str, Any]] = {}
    for case in ordered:
        regime = str(case.get("regime") or "unknown")
        bucket = regimes.setdefault(regime, {"sample_size": 0, "wins": 0, "regime_label": case.get("regime_label") or regime})
        bucket["sample_size"] += 1
        bucket["wins"] += 1 if _case_win(case) else 0
    for bucket in regimes.values():
        wins_count = bucket.pop("wins")
        sample = bucket["sample_size"]
        bucket["win_1r_pct"] = round(wins_count / sample * 100, 1) if sample else None
        bucket["win_1r_ci"] = bootstrap_ci_from_counts(wins_count, sample, iterations=iterations, confidence=confidence)
    period = _period(ordered)
    return {
        **stat,
        "win_1r_ci": list(ci) if ci else None,
        "oos": oos,
        "unstable": bool(oos and oos.get("unstable")),
        "walk_forward": curve,
        "regimes": regimes,
        "period": period,
        "disclaimer": DISCLAIMER_NET,
    }


def format_stat_line(
    stat: dict[str, Any],
    *,
    sample_floor: int = 10,
    current_regime: str | None = None,
    label: str | None = None,
    metric_label: str = "net 1R",
) -> str:
    """표기 표준 (docs/Statistics.md): `net 승률 (CI, N, 기간, 레짐, unstable 여부)`.

    모든 승률 발행 표면은 이 함수를 거친다 — CI 없는 승률 표기 금지.
    """
    stat_label = label or stat.get("label") or "동일 시그니처"
    n = int(stat.get("sample_size") or 0)
    if n < sample_floor:
        return f"{stat_label} 표본 부족 (N={n}) — 결론 유보"
    regime_stat, regime_note = _regime_slice(stat, current_regime, sample_floor)
    win = regime_stat.get("win_1r_pct")
    ci = regime_stat.get("win_1r_ci")
    regime_n = int(regime_stat.get("sample_size") or 0)
    if win is None or not ci:
        return f"{stat_label} CI 미산출 (N={regime_n}) — 발행 보류"
    parts = [f"{stat_label} {metric_label} {win}% (CI {ci[0]}~{ci[1]}%, N={regime_n}"]
    period = stat.get("period")
    if isinstance(period, dict) and period.get("label"):
        parts.append(f", {period['label']}")
    if regime_note:
        parts.append(f", {regime_note}")
    parts.append(")")
    line = "".join(parts)
    if stat.get("unstable"):
        line += " · OOS 불안정"
    return line


def _regime_slice(stat: dict[str, Any], current_regime: str | None, sample_floor: int) -> tuple[dict[str, Any], str | None]:
    """현재 레짐 슬라이스 표본이 충분하면 그 통계를 우선 표기한다."""
    regimes = stat.get("regimes") if isinstance(stat.get("regimes"), dict) else {}
    if current_regime and current_regime in regimes:
        slice_stat = regimes[current_regime]
        if int(slice_stat.get("sample_size") or 0) >= sample_floor and slice_stat.get("win_1r_ci"):
            return slice_stat, f"레짐 {slice_stat.get('regime_label') or current_regime}"
    if current_regime:
        return stat, f"전체 레짐 (현재 {current_regime} 표본 부족)"
    return stat, None


def _sorted_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(cases, key=lambda case: str(case.get("as_of") or ""))


def _case_win(case: dict[str, Any]) -> bool:
    outcome = case.get("outcome") if isinstance(case.get("outcome"), dict) else {}
    return bool(outcome.get("win_1r"))


def _win_rate(cases: list[dict[str, Any]]) -> float | None:
    if not cases:
        return None
    return round(sum(1 for case in cases if _case_win(case)) / len(cases) * 100, 1)


def _case_dt(case: dict[str, Any]) -> datetime | None:
    value = case.get("as_of")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _period(cases: list[dict[str, Any]]) -> dict[str, Any] | None:
    stamps = [stamp for stamp in (_case_dt(case) for case in cases) if stamp is not None]
    if not stamps:
        return None
    start, end = min(stamps), max(stamps)
    days = max(1, (end - start).days)
    # "최근 N일"은 데이터가 오래됐을 때 현재성을 사칭한다 — 명시적 구간으로 표기.
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": days,
        "label": f"{start.date().isoformat()}~{end.date().isoformat()}",
    }
