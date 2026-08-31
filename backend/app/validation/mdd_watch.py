"""WO-FCE-MAKE-IT-RUN-01 Phase 4 — MDD 서명값 초과 관측.

## 왜 이 모듈이 생겼나

크립토 MDD 가 **20.62%** 로 서명값 20% 를 넘겼다. 그런데 화면·리포트에는 **숫자만 있고
초과 표시가 없었다** — 넘긴 사실이 어디에도 안 나온다.

> **임계를 올리지 않는다**(C2). 20% 는 서명값이다. 이 모듈은 초과를 **보이게 할 뿐**이다.

## 게이트가 아니다

`live_trading_gate` 는 6축이고 MDD 축이 없다. 코드에 존재하는 유일한 MDD 설정
`FCE_PERFORMANCE_MONTHLY_MDD_LIMIT_PCT` 는 기본값 **0.0(비활성)** 이다.

그래서 여기 있는 `SIGNED_MDD_CEILING_PCT` 는 **표시용 상수**다. 진입을 막지도, 전환을
차단하지도 않는다 — 그것을 하려면 별도 결정이 필요하고 이 WO 범위가 아니다.
상수를 게이트로 승격시키면 그것이야말로 서명값을 임의로 바꾸는 것이다.

## 낙폭 구간을 특정한다

"MDD 20.62%" 만으로는 대응할 수 없다. **언제·어느 거래에서 났는가**가 있어야 원인을
볼 수 있다. `drawdown_window()` 가 고점→저점 구간과 그 안의 거래를 낸다.

## 포트폴리오 상한 반사실

`RISK-SIZING-01` Phase 4 의 동시 보유 상한이 이 낙폭을 막았을지 **계산만** 한다.
실제 포지션을 바꾸지 않으며(C7) 그 결과는 성적이 아니라 대조군이다(C8).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

# **서명값이다. 올리지 않는다**(C2). 표시용이며 게이트가 아니다.
SIGNED_MDD_CEILING_PCT = 20.0
# 이 안에 들면 "근접"으로 표시한다. 넘기고 나서 아는 것보다 낫다.
WARN_MARGIN_PP = 2.0


def mdd_status(mdd_pct: float | None) -> dict[str, Any]:
    """서명값 대비 현재 MDD. **초과를 숫자 옆에 붙여 다니게 한다.**"""
    if mdd_pct is None:
        return {"state": "unknown", "ceiling_pct": SIGNED_MDD_CEILING_PCT, "label": None, "note": "MDD 미산출"}
    value = float(mdd_pct)
    margin = round(SIGNED_MDD_CEILING_PCT - value, 4)
    if value > SIGNED_MDD_CEILING_PCT:
        return {
            "state": "breached",
            "mdd_pct": value,
            "ceiling_pct": SIGNED_MDD_CEILING_PCT,
            "margin_pp": margin,
            "label": f"⚠️ 서명값 {SIGNED_MDD_CEILING_PCT:g}% 초과",
            "note": "임계를 올리지 않는다 — 초과 사실을 표시할 뿐이다(C2). 전환 게이트를 차단하지는 않는다.",
        }
    if margin <= WARN_MARGIN_PP:
        return {
            "state": "near",
            "mdd_pct": value,
            "ceiling_pct": SIGNED_MDD_CEILING_PCT,
            "margin_pp": margin,
            "label": f"서명값까지 {margin:.2f}%p",
            "note": "근접 표시다. 넘기고 나서 아는 것보다 낫다.",
        }
    return {"state": "ok", "mdd_pct": value, "ceiling_pct": SIGNED_MDD_CEILING_PCT, "margin_pp": margin, "label": None, "note": None}


def _stamp(trade: Any) -> datetime | None:
    return getattr(trade, "exit_at", None) or getattr(trade, "exit_bar_at", None)


def drawdown_window(trades: list[Any]) -> dict[str, Any]:
    """최대 낙폭이 **언제·어느 거래에서** 났는가 (Phase 4 항목 2).

    청산 시각 순으로 누적 손익을 쌓고 고점 대비 최대 하락 구간을 찾는다. `_metric_payload`
    의 MDD 와 같은 정의(금액 기준 고점 대비 하락)를 쓴다 — 정의가 갈리면 두 수가 서로를
    설명하지 못한다.

    **표본이 없으면 구간을 만들지 않는다**(C8).
    """
    closed = sorted(
        (trade for trade in trades if getattr(trade, "status", "") == "closed" and _stamp(trade) is not None),
        key=lambda item: _stamp(item),
    )
    if not closed:
        return {"available": False, "reason": "청산 표본이 없다 — 낙폭 구간을 만들지 않는다"}

    equity = 0.0
    peak = 0.0
    peak_index = 0
    worst = 0.0
    start = end = 0
    for index, trade in enumerate(closed):
        equity += float(getattr(trade, "net_pnl_usdt", 0.0) or 0.0)
        if equity > peak:
            peak = equity
            peak_index = index
        drop = peak - equity
        if drop > worst:
            worst = drop
            start, end = peak_index, index
    if worst <= 0:
        return {"available": True, "drawdown_usdt": 0.0, "note": "고점 대비 하락이 없다"}

    segment = closed[start : end + 1]
    return {
        "available": True,
        "drawdown_usdt": round(worst, 4),
        "peak_equity_usdt": round(peak, 4),
        "from": _stamp(closed[start]).isoformat(),
        "to": _stamp(closed[end]).isoformat(),
        "trade_count": len(segment),
        "symbols": sorted({str(getattr(trade, "symbol", "")) for trade in segment}),
        "worst_trades": [
            {
                "id": str(getattr(trade, "id", "")),
                "symbol": getattr(trade, "symbol", None),
                "net_pnl_usdt": round(float(getattr(trade, "net_pnl_usdt", 0.0) or 0.0), 4),
                "exit_reason": getattr(trade, "exit_reason", None),
            }
            for trade in sorted(segment, key=lambda item: float(getattr(item, "net_pnl_usdt", 0.0) or 0.0))[:5]
        ],
    }


def concurrent_cap_counterfactual(trades: list[Any], *, max_concurrent: int) -> dict[str, Any]:
    """동시 보유 상한이 이 낙폭을 **줄였을까** (Phase 4 항목 4).

    ## 무엇을 계산하는가

    진입 시각 순으로 훑으면서 이미 열려 있는 포지션이 상한에 도달했으면 그 거래를
    **열지 않았다고 가정**하고 누적 손익을 다시 쌓는다. 그 위에서 MDD 를 다시 잰다.

    ## 무엇이 아닌가 (C8)

    **성적이 아니다.** 막힌 거래 대신 다른 거래가 들어왔을 수도 있고, 상한이 있었다면
    진입 순서 자체가 달랐을 수도 있다. 이것은 "그 상한이 이 낙폭에 닿았는가"라는
    좁은 질문의 답이며 **인과 단정이 아니다**.
    """
    rows = sorted(
        (trade for trade in trades if getattr(trade, "status", "") == "closed" and getattr(trade, "entry_at", None) is not None and _stamp(trade) is not None),
        key=lambda item: item.entry_at,
    )
    if not rows:
        return {"available": False, "reason": "청산 표본이 없다"}

    open_until: list[datetime] = []
    kept: list[Any] = []
    skipped = 0
    for trade in rows:
        open_until = [stamp for stamp in open_until if stamp > trade.entry_at]
        if len(open_until) >= max(1, int(max_concurrent)):
            skipped += 1
            continue
        open_until.append(_stamp(trade))
        kept.append(trade)

    def _mdd(items: list[Any]) -> float:
        equity = peak = worst = 0.0
        for item in sorted(items, key=lambda value: _stamp(value)):
            equity += float(getattr(item, "net_pnl_usdt", 0.0) or 0.0)
            peak = max(peak, equity)
            worst = max(worst, peak - equity)
        return round(worst, 4)

    actual = _mdd(rows)
    capped = _mdd(kept)
    return {
        "available": True,
        "max_concurrent": int(max_concurrent),
        "actual_mdd_usdt": actual,
        "capped_mdd_usdt": capped,
        "delta_usdt": round(capped - actual, 4),
        "skipped_entries": skipped,
        "kept_entries": len(kept),
        "not_performance": "반사실이다 — 막힌 거래 대신 다른 거래가 들어왔을 수 있다. 성적으로 보고하지 않는다(C8).",
    }
