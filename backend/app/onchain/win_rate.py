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


def selection_disclosure(win_rates: dict[str, dict[str, Any]], *, min_sample: int = MIN_SAMPLE) -> dict[str, Any]:
    """화면·알림이 "승률로 뽑았다"로 오해되지 않게 하는 고지(5-4).

    ## 집계 방식이 값과 함께 다닌다 (WO-FCE-REPORT-DEFECTS-01 7-3)

    `overall_win_rate_pct` 는 **체결 가중**이다 — 전체 승 ÷ 전체 체결이며 표본 미달 지갑도
    포함한다. 지갑 평균이 아니다. 체결이 많은 소수 지갑이 값을 끌고 갈 수 있다.

    그래서 지갑 중앙값(`wallet_median_win_rate_pct`)을 **함께** 낸다. 두 값이 크게 벌어지면
    그 자체가 분포가 치우쳤다는 신호다 — 하나만 보이면 그것을 알 수 없다.

    `min_sample` 은 **호출부가 넘긴다.** 넘기지 않으면 이 모듈 기본값이 쓰이는데, 라벨을
    찍는 쪽이 다른 수를 들고 있으면 라벨이 거짓이 된다 — 실측에서 그랬다(라벨 30 · 실계산 20).
    """
    scored = [row for row in win_rates.values() if int(row["sample_size"]) >= min_sample]
    total = sum(int(row["sample_size"]) for row in win_rates.values())
    wins = sum(int(row["wins"]) for row in win_rates.values())
    rates = sorted(round(int(row["wins"]) / int(row["sample_size"]) * 100, 1) for row in scored if int(row["sample_size"]))
    return {
        "selection_basis": "quality_score (월간 PnL · ROI · 계좌규모)",
        "win_rate_role": "사후 채점 지표 — 선정에 사용하지 않음",
        "why": "승률 4.3%로 +$2.1M, 승률 51.1%로 −$416K 인 지갑이 실재한다. 승률만으로 뽑으면 비대칭 손익 트레이더를 강등시킨다.",
        "scored_wallets": len(scored),
        "total_wallets": len(win_rates),
        "closed_samples": total,
        "overall_win_rate_pct": round(wins / total * 100, 1) if total else None,
        # 7-3 항목 1 — 계산 방식을 값 옆에 둔다. 방식 없는 승률은 읽는 사람이 지갑 평균으로 읽는다.
        "overall_win_rate_basis": "fill_weighted",
        "overall_win_rate_note": "전체 승 ÷ 전체 체결 — **체결 가중**이며 표본 미달 지갑을 포함한다. 지갑 평균이 아니다.",
        "overall_population": "closed_pnl 이 있는 전체 지갑",
        "wallet_median_win_rate_pct": rates[len(rates) // 2] if rates else None,
        "wallet_median_population": f"표본 {min_sample}건 이상 지갑 {len(scored)}개",
        "min_sample": int(min_sample),
        "label": f"선정: quality_score(PnL·ROI·규모) · 승률은 사후 채점(N={total:,})",
    }
