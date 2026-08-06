"""WO-FCE-OBSERVATION-INTEGRITY-01 Phase 5 — 고래 승률 사후 채점.

## 5-1 승률 데이터 가용성: **가능**

하이퍼리퀴드 리더보드 응답에는 승률 필드가 없다(`windowPerformances` 는 pnl·roi·vlm 뿐).
그러나 `userFillsByTime` 이 체결별 `closedPnl` 을 주고, 수집기가 이미 그것을
`whale_events.payload.payload.closed_pnl` 로 저장하고 있다.

실측 2026-08-05: `close`/`reduce` 이벤트 **6,382건 전량(100%)** 에 `closed_pnl` 존재.
지갑 62개, 표본 20건 이상 33개. 전체 승률 64.7%.

## 5-2/5-3 왜 quality_score 에 승률 항을 넣지 않는가

넣을 수 **있지만** 실측이 넣지 말라고 말한다. 승률과 수익성이 역상관인 사례가 실재한다:

| 지갑 | 승률 | 누적 closed PnL |
| --- | --- | --- |
| `0xd04f9719…` | **4.3%** (n=47) | **+2,108,265** |
| `0x2437529…` | 100.0% (n=204) | +1,227,485 |
| `0x77375a8c…` | 51.1% (n=174) | **−415,726** |
| `0xfc667adb…` | 36.7% (n=251) | −135,835 |

승률 4.3%로 200만 달러를 번 지갑은 비대칭 손익 트레이더다. 승률 항을 점수에 더했다면
이 지갑이 강등됐을 것이다. **승률은 선정 기준이 아니라 사후 채점 지표다.**

따라서 이 모듈은 관측만 한다 — `quality_score` 를 바꾸지 않고 병행 지표(A/B)로 노출한다.
선별 기준 변경은 이 관측이 충분히 쌓인 뒤 별도 WO 에서 판단한다.
"""

from __future__ import annotations

import json
from typing import Any

# 이 수 미만이면 승률을 판정하지 않는다 — 표본 부족을 성적으로 발표하지 않는다.
MIN_SAMPLE = 20
CLOSING_EVENTS = ("close", "reduce")


def _closed_pnl(payload: Any) -> float | None:
    """이벤트 payload 에서 실현 손익을 꺼낸다. 저장 구조는 2단 중첩이다."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return None
    if not isinstance(payload, dict):
        return None
    inner = payload.get("payload")
    if isinstance(inner, str):
        try:
            inner = json.loads(inner)
        except (TypeError, ValueError):
            inner = None
    for source in (inner, payload):
        if not isinstance(source, dict):
            continue
        for key in ("closed_pnl", "closedPnl"):
            raw = source.get(key)
            if raw in (None, ""):
                continue
            try:
                return float(raw)
            except (TypeError, ValueError):
                continue
        raw_fill = source.get("raw")
        if isinstance(raw_fill, dict) and raw_fill.get("closedPnl") not in (None, ""):
            try:
                return float(raw_fill["closedPnl"])
            except (TypeError, ValueError):
                continue
    return None


def _field(event: Any, *names: str) -> Any:
    """호출부가 dict(원장 조회)와 pydantic 모델(레포 조회) 양쪽을 넘긴다 — 둘 다 받는다."""
    for name in names:
        if isinstance(event, dict):
            if event.get(name) is not None:
                return event[name]
        else:
            value = getattr(event, name, None)
            if value is not None:
                return value
    return None


def observed_win_rates(events: list[Any], *, min_sample: int = MIN_SAMPLE) -> dict[str, dict[str, Any]]:
    """지갑별 사후 관측 승률. 표본 미달이면 `win_rate_pct=None` — 모르면 모른다고 낸다."""
    tally: dict[str, dict[str, float]] = {}
    for event in events:
        if str(_field(event, "event_type", "event") or "").lower() not in CLOSING_EVENTS:
            continue
        address = str(_field(event, "wallet_address") or "").lower()
        if not address:
            continue
        pnl = _closed_pnl(_field(event, "payload"))
        if pnl is None:
            continue
        bucket = tally.setdefault(address, {"n": 0.0, "wins": 0.0, "pnl": 0.0})
        bucket["n"] += 1
        bucket["pnl"] += pnl
        if pnl > 0:
            bucket["wins"] += 1
    result: dict[str, dict[str, Any]] = {}
    for address, bucket in tally.items():
        count = int(bucket["n"])
        enough = count >= min_sample
        result[address] = {
            "sample_size": count,
            "wins": int(bucket["wins"]),
            "win_rate_pct": round(bucket["wins"] / count * 100, 1) if enough else None,
            "closed_pnl_usd": round(bucket["pnl"], 2),
            "sample_low": not enough,
            # 승률이 높다고 수익이 높지 않다 — 실측에서 역상관 사례가 나왔다.
            "profitable": bucket["pnl"] > 0,
            "basis": "hyperliquid userFillsByTime closedPnl (사후 관측)",
        }
    return result


def selection_disclosure(win_rates: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """화면·알림이 "승률로 뽑았다"로 오해되지 않게 하는 고지(5-4)."""
    scored = [row for row in win_rates.values() if not row["sample_low"]]
    total = sum(int(row["sample_size"]) for row in win_rates.values())
    wins = sum(int(row["wins"]) for row in win_rates.values())
    return {
        "selection_basis": "quality_score (월간 PnL · ROI · 계좌규모)",
        "win_rate_role": "사후 채점 지표 — 선정에 사용하지 않음",
        "why": "승률 4.3%로 +$2.1M, 승률 51.1%로 −$416K 인 지갑이 실재한다. 승률만으로 뽑으면 비대칭 손익 트레이더를 강등시킨다.",
        "scored_wallets": len(scored),
        "total_wallets": len(win_rates),
        "closed_samples": total,
        "overall_win_rate_pct": round(wins / total * 100, 1) if total else None,
        "min_sample": MIN_SAMPLE,
        "label": f"선정: quality_score(PnL·ROI·규모) · 승률은 사후 채점(N={total:,})",
    }
