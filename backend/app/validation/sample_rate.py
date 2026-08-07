"""WO-FCE-SAMPLE-RATE-01 Phase 1 — 표본 생성 속도 격차 정량화.

## 왜 이 모듈이 생겼나

지난 세 WO로 관측·측정·판정 장치가 갖춰졌고, 그 장치가 답을 냈다: **표본 생성 속도가
검증 완료 기준에 미달한다.** 더 이상 새 결함을 찾는 단계가 아니다. 이제 "30건을 어떻게
모을 것인가"에 답해야 하고, 그 답은 **기준을 낮추지 않고** 나와야 한다.

## 진입만 봐서는 오진한다

`sample_viability` 는 진입률과 청산 완료율을 낸다. 그런데 "무엇을 얼마나 바꿔야 하는가"에
답하려면 **보유기간**이 필요하다. 슬롯이 유한하므로 표본 처리량은 다음으로 제한된다:

```
동시 보유 한도(C) 기준 최대 표본 = C × 기간(D) / 평균 보유기간(H)
```

진입률을 아무리 올려도 슬롯이 차 있으면 진입이 일어나지 않는다. 반대로 보유기간을 줄이면
같은 슬롯이 더 자주 회전해 표본이 늘어난다. **세 축(진입률·관측일·보유기간)을 함께 봐야
부족분을 무엇으로 메울지 정할 수 있다.**

## 이 모듈이 하지 않는 것

- 임계값을 바꾸지 않는다. 역산 결과는 **제안**이며 적용은 별도 판단이다.
- 표본이 부족하면 부족하다고 낸다. 목표치를 낮춰 격차를 없애지 않는다.
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from typing import Any

from app.validation import sample_viability

# 보유기간 표본이 이보다 적으면 평균을 신뢰하지 않는다 — 1~2건의 평균은 숫자가 아니다.
MIN_HOLDING_SAMPLES = 3


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _rows(connection: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    try:
        return connection.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []


def holding_intervals(connection: sqlite3.Connection, track: str) -> list[tuple[datetime, datetime]]:
    """완결된 (진입, 청산) 구간. 보유기간과 동시 보유 수를 여기서 함께 얻는다.

    주식은 원장에 거래 단위가 없고 체결만 있으므로 **심볼별로 매수→매도를 순서대로 짝짓는다.**
    짝이 맞지 않는 체결(먼저 온 매도 등)은 버린다 — 억지로 짝지으면 없는 거래가 생긴다.
    """
    pairs: list[tuple[datetime, datetime]] = []
    if track == "crypto":
        for row in _rows(connection, "SELECT entry_bar_at, exit_at FROM paper_trades WHERE status='closed' AND exit_at IS NOT NULL"):
            entry, exit_at = _parse(row["entry_bar_at"]), _parse(row["exit_at"])
            if entry and exit_at and exit_at >= entry:
                pairs.append((entry, exit_at))
    elif track == "poly":
        for row in _rows(connection, "SELECT opened_at, resolved_at FROM poly_positions WHERE status='resolved' AND resolved_at IS NOT NULL"):
            entry, exit_at = _parse(row["opened_at"]), _parse(row["resolved_at"])
            if entry and exit_at and exit_at >= entry:
                pairs.append((entry, exit_at))
    elif track in {"stock_kr", "stock_us"}:
        market = "KR" if track == "stock_kr" else "US"
        open_by_symbol: dict[str, list[datetime]] = {}
        for row in _rows(
            connection,
            "SELECT symbol, side, filled_at FROM stock_paper_fills WHERE market=? ORDER BY filled_at",
            (market,),
        ):
            stamp = _parse(row["filled_at"])
            if stamp is None:
                continue
            symbol = str(row["symbol"])
            if str(row["side"]) == "buy":
                open_by_symbol.setdefault(symbol, []).append(stamp)
            elif open_by_symbol.get(symbol):
                pairs.append((open_by_symbol[symbol].pop(0), stamp))
    return sorted(pairs)


def holding_profile(intervals: list[tuple[datetime, datetime]]) -> dict[str, Any]:
    """평균·중앙값 보유기간과 **관측된 동시 보유 최대치**.

    동시 보유 최대치는 슬롯 한도의 하한 추정이다. 설정값을 읽지 않고 원장에서 재는 이유는,
    설정 한도가 실제로 다 쓰이고 있는지가 바로 이 WO 의 질문이기 때문이다.
    """
    if len(intervals) < MIN_HOLDING_SAMPLES:
        return {
            "n": len(intervals),
            "mean_days": None,
            "median_days": None,
            "observed_max_concurrent": None,
            "reason": f"보유 표본 {len(intervals)}건 < {MIN_HOLDING_SAMPLES}건 — 평균을 신뢰할 수 없다",
        }
    durations = sorted((exit_at - entry).total_seconds() / 86_400 for entry, exit_at in intervals)
    events: list[tuple[datetime, int]] = []
    for entry, exit_at in intervals:
        events.append((entry, 1))
        events.append((exit_at, -1))
    events.sort()
    concurrent = peak = 0
    for _, delta in events:
        concurrent += delta
        peak = max(peak, concurrent)
    return {
        "n": len(durations),
        "mean_days": round(sum(durations) / len(durations), 3),
        "median_days": round(durations[len(durations) // 2], 3),
        "observed_max_concurrent": peak,
        "reason": None,
    }


def _shortfall_inversion(
    *,
    shortfall: int,
    entries_per_effective_day: float,
    exit_conversion: float,
    remaining_days: int,
    holding: dict[str, Any],
) -> dict[str, Any]:
    """부족분을 메우려면 무엇이 얼마나 필요한가 — 세 축 각각 (Phase 1-5).

    세 축은 **대안**이지 합산 대상이 아니다. 하나로 메우거나 섞거나는 판단의 영역이다.
    """
    samples_per_effective_day = entries_per_effective_day * exit_conversion
    axes: dict[str, Any] = {}

    # 축 1 — 진입률: 남은 유효일은 그대로 두고 진입만 늘린다.
    if samples_per_effective_day > 0 and remaining_days > 0:
        needed_rate = shortfall / (remaining_days * exit_conversion) if exit_conversion > 0 else None
        axes["entry_rate"] = {
            "current_per_effective_day": entries_per_effective_day,
            "required_per_effective_day": round(needed_rate, 3) if needed_rate else None,
            "multiplier": round(needed_rate / entries_per_effective_day, 2) if needed_rate and entries_per_effective_day > 0 else None,
        }
    else:
        axes["entry_rate"] = {"required_per_effective_day": None, "multiplier": None, "reason": "진입·청산 실적이 없어 역산 불가"}

    # 축 2 — 관측일: 속도는 그대로 두고 기간을 늘린다.
    axes["effective_days"] = (
        {"additional_effective_days": math.ceil(shortfall / samples_per_effective_day)}
        if samples_per_effective_day > 0
        else {"additional_effective_days": None, "reason": "표본 생성 속도 0 — 기간을 늘려도 표본이 늘지 않는다"}
    )

    # 축 3 — 보유기간: 슬롯 회전. 최대 표본 = 동시 보유 한도 × 기간 / 보유기간.
    capacity = holding.get("observed_max_concurrent")
    mean_days = holding.get("mean_days")
    if capacity and mean_days and remaining_days > 0 and shortfall > 0:
        required_holding = capacity * remaining_days / shortfall
        axes["holding_period"] = {
            "observed_capacity": capacity,
            "current_mean_days": mean_days,
            "required_mean_days": round(required_holding, 3),
            "reduction_pct": round(max(0.0, (1 - required_holding / mean_days)) * 100, 1) if mean_days > 0 else None,
            "already_sufficient": required_holding >= mean_days,
        }
    else:
        axes["holding_period"] = {"required_mean_days": None, "reason": "보유기간 또는 동시 보유 실적이 없어 역산 불가"}
    return axes


def track_rate_gap(
    connection: sqlite3.Connection,
    track: str,
    *,
    now: datetime | None = None,
    target_days: int = sample_viability.TARGET_EFFECTIVE_DAYS,
    target_samples: int = sample_viability.TARGET_SAMPLES,
) -> dict[str, Any]:
    """트랙 하나의 속도 격차와 부족분 역산 (Phase 1).

    분모는 `sample_viability` 와 같은 **유효 관측일**이다. 두 모듈이 다른 분모를 쓰면
    같은 화면에서 앞뒤가 안 맞는 숫자가 나온다.
    """
    now = now or datetime.now(timezone.utc)
    viability = sample_viability.track_sample_viability(connection, track, now=now, target_days=target_days, target_samples=target_samples)
    holding = holding_profile(holding_intervals(connection, track))

    remaining_days = max(0, target_days - int(viability["effective_days"]))
    rate = float(viability["entries_per_effective_day"])
    rate_low = float(viability["entries_per_effective_day_ci95"][0])
    conversion = float(viability["exit_completion_rate"])
    scored = int(viability["scored_samples"])

    projected = int(scored + rate * remaining_days * conversion)
    projected_low = int(scored + rate_low * remaining_days * conversion)
    shortfall = max(0, target_samples - projected)

    return {
        "track": track,
        "label": viability["label"],
        "effective_days": viability["effective_days"],
        "effective_days_target": target_days,
        "remaining_effective_days": remaining_days,
        "entries_per_effective_day": rate,
        "entries_per_effective_day_ci95": viability["entries_per_effective_day_ci95"],
        "exit_conversion_rate": conversion,
        "holding": holding,
        "current_samples": scored,
        "projected_samples": projected,
        # CI 하한 기준 예상도 함께 낸다 — 점추정만 보면 낙관에 기댄 계획이 된다.
        "projected_samples_ci_low": projected_low,
        "target_samples": target_samples,
        "shortfall": shortfall,
        "shortfall_ci_low": max(0, target_samples - projected_low),
        "inversion": _shortfall_inversion(
            shortfall=shortfall,
            entries_per_effective_day=rate,
            exit_conversion=conversion,
            remaining_days=remaining_days,
            holding=holding,
        ),
        "verdict": viability["verdict"],
        "statement": viability["statement"],
    }


def rate_gap_report(
    connection: sqlite3.Connection,
    *,
    now: datetime | None = None,
    target_days: int = sample_viability.TARGET_EFFECTIVE_DAYS,
    target_samples: int = sample_viability.TARGET_SAMPLES,
) -> dict[str, Any]:
    """4트랙 속도 격차 표 (Phase 1-4)."""
    now = now or datetime.now(timezone.utc)
    tracks = {
        track: track_rate_gap(connection, track, now=now, target_days=target_days, target_samples=target_samples)
        for track in sample_viability.TRACK_SAMPLE_SPECS
    }
    return {
        "as_of": now.isoformat(),
        "principle": "부족분은 기준을 낮춰서가 아니라 속도·기간·회전으로 메운다.",
        "target_samples": target_samples,
        "tracks": tracks,
        "tracks_with_shortfall": [track for track, row in tracks.items() if row["shortfall"] > 0],
    }


def _payload(row: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(row["payload"]))
    except (ValueError, TypeError, IndexError, KeyError):
        return {}
    return value if isinstance(value, dict) else {}


__all__ = [
    "MIN_HOLDING_SAMPLES",
    "holding_intervals",
    "holding_profile",
    "rate_gap_report",
    "track_rate_gap",
]
