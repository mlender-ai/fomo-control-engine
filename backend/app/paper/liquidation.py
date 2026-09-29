"""강제청산 모델 (FOMO LAB ENG-01) — 페이퍼가 거래소와 같은 규칙으로 청산된다.

## 왜

이전에는 증거금이 바닥나도 포지션이 살아 있었다(`evaluate_exit` 에 청산 분기 없음). 버티다 반등하면 이익으로
기록된다 — 페이퍼가 실제보다 **무조건** 좋게 나온다.

## 출처 — Bitget (페이퍼가 흉내 내는 거래소). 공식을 지어내지 않는다

- 청산가(격리): Bitget Support "What Is Estimated Liquidation Price in Bitget Futures?" (articles/12560603808759)

      청산가 = [포지션 증거금 + 단계 공제 − 수량 × 진입가 × 방향] ÷ [수량 × (MMR + 테이커 수수료율 − 방향)]
      방향 = 롱 1 · 숏 −1

- MMR: `/api/v2/mix/market/query-position-lever` 의 `keepMarginRate`(명목 단계). 페이퍼 명목은 늘 1단계 — 공제 0
- 테이커 수수료율: 0.0006 (`/api/v2/mix/market/contracts` `takerFeeRate`)
- 마진 모드: 페이퍼는 포지션마다 증거금(`margin_usdt`)을 따로 둔다 — **격리**

## 펀딩

누적 펀딩을 격리 증거금에서 뺀다 → 청산가가 진입가 쪽으로 온다. 정산(8시간)마다 다시 잰다.
**손익에는 세지 않는다** — 페이퍼 손익 모형은 그대로 두고 청산가에만 쓴다(손익에 넣는 것은 별건 결정).

## 체결

| | |
|---|---|
| 봉 저가(롱)·고가(숏)가 청산가에 닿음 | 그 봉에서 청산가로 |
| 봉 시가가 이미 청산가 너머 (갭) | 시가로 — 청산가보다 나쁘다 |
| 같은 봉에서 손절선도 닿음 | 손절선이 청산가보다 진입가에 가까우면 **손절 먼저**, 아니면 청산 |
| 청산되면 | 남은 격리 증거금 전부를 잃는다 · 청산 수수료는 공식의 수수료율로 이미 들어 있다 |

## invariant

**포지션 손실(가격 손익)은 증거금을 넘을 수 없다.** 넘으면 그 전에 청산됐어야 한다 — 위반이면 트랙을 멈춘다.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BITGET_API = "https://api.bitget.com/api/v2/mix/market"
TAKER_FEE_RATE = 0.0006
# Bitget 단계를 못 받았을 때 — FCE 진입 시뮬레이터(`positions/simulator.py`)의 기존 기본값. 출처를 거래에 남긴다.
FALLBACK_MMR = 0.005
TIER_TTL_SECONDS = 24 * 3600
FUNDING_TTL_SECONDS = 3600

_tiers: dict[str, tuple[float, list[dict[str, float]]]] = {}
_funding: dict[str, tuple[float, list[tuple[int, float]]]] = {}


class PositionLossInvariantViolation(RuntimeError):
    """포지션 손실이 증거금을 넘었다 — 청산 모델이 그 전에 청산했어야 한다."""


def _direction(value: Any) -> int:
    text = str(getattr(value, "value", value)).lower()
    return -1 if text == "short" else 1


def liquidation_price(*, direction: Any, entry: float, quantity: float, margin: float, mmr: float, taker_fee: float = TAKER_FEE_RATE, offset: float = 0.0) -> float | None:
    """Bitget 격리 청산가. 성립하지 않으면 None."""
    d = _direction(direction)
    denom = quantity * (mmr + taker_fee - d)
    if quantity <= 0 or denom == 0:
        return None
    price = (margin + offset - quantity * entry * d) / denom
    return max(0.0, price)


def _offline() -> bool:
    """테스트 중이거나 `FCE_LIQUIDATION_OFFLINE=1` 이면 거래소를 부르지 않는다 — 기본값 · 펀딩 0 으로 잰다."""
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) or os.environ.get("FCE_LIQUIDATION_OFFLINE") == "1"


def _get(path: str, params: dict[str, Any]) -> list[Any]:
    if _offline():
        raise RuntimeError("liquidation network disabled")
    with httpx.Client(timeout=6.0) as client:
        response = client.get(f"{BITGET_API}{path}", params=params)
        body = response.json()
    if body.get("code") != "00000":
        raise RuntimeError(f"bitget {path} {body.get('msg')}")
    return body.get("data") or []


def maintenance_margin_rate(symbol: str, notional: float) -> tuple[float, str]:
    """(MMR, 출처). 단계는 종목별 24시간 캐시. 못 받으면 마지막으로 받은 값 → 그것도 없으면 FCE 기본값."""
    now = time.time()
    hit = _tiers.get(symbol)
    if hit is None or now - hit[0] > TIER_TTL_SECONDS:
        try:
            rows = _get("/query-position-lever", {"symbol": symbol, "productType": "USDT-FUTURES"})
            tiers = [
                {"start": float(r["startUnit"]), "end": float(r["endUnit"]), "rate": float(r["keepMarginRate"])}
                for r in rows
                if isinstance(r, dict) and r.get("keepMarginRate") is not None
            ]
            if tiers:
                hit = (now, sorted(tiers, key=lambda t: t["start"]))
                _tiers[symbol] = hit
        except Exception as exc:  # noqa: BLE001 — 청산가는 기본값으로라도 계속 잰다
            logger.warning("liquidation tiers %s: %s", symbol, exc)
    if hit is None:
        return FALLBACK_MMR, "fce_default"
    tiers = hit[1]
    chosen = next((t for t in tiers if t["start"] <= notional < t["end"]), tiers[-1])
    return chosen["rate"], "bitget_tier"


def funding_settlements(symbol: str, since_ms: int) -> list[tuple[int, float]]:
    """(정산 시각 ms, 펀딩률) — 1시간 캐시. 못 받으면 빈 목록(펀딩 0 으로 — 청산가가 덜 보수적이 된다는 것을 로그로 남긴다)."""
    now = time.time()
    hit = _funding.get(symbol)
    if hit is not None and now - hit[0] <= FUNDING_TTL_SECONDS and (not hit[1] or hit[1][0][0] <= since_ms):
        return hit[1]
    points: list[tuple[int, float]] = []
    try:
        for page in range(1, 6):
            rows = _get("/history-fund-rate", {"symbol": symbol, "productType": "USDT-FUTURES", "pageSize": 100, "pageNo": page})
            if not rows:
                break
            points.extend((int(r["fundingTime"]), float(r["fundingRate"])) for r in rows if isinstance(r, dict))
            if min(int(r["fundingTime"]) for r in rows) <= since_ms:
                break
    except Exception as exc:  # noqa: BLE001
        logger.warning("liquidation funding %s: %s", symbol, exc)
        return hit[1] if hit else []
    points = sorted(set(points))
    _funding[symbol] = (now, points)
    return points


def accrue_funding(trade: Any, *, until_ms: int, price: float) -> dict[str, Any]:
    """이 거래가 `until_ms` 까지 낸 펀딩(양수 = 냈다). 정산 시각의 가격은 모르므로 지금 봉 가격으로 잰다(근사)."""
    entry_ms = int(trade.entry_at.timestamp() * 1000)
    settled = [(at, rate) for at, rate in funding_settlements(trade.symbol, entry_ms) if entry_ms < at <= until_ms]
    paid = sum(trade.remaining_quantity * price * rate * _direction(trade.direction) for _, rate in settled)
    return {"funding_paid_usdt": paid, "funding_settlements": len(settled)}


def trade_liquidation(trade: Any, *, mmr: float, funding_paid: float) -> float | None:
    """남은 수량 · 그 몫의 격리 증거금(부분 익절 뒤엔 비례로 줄어든다) − 펀딩으로 잰 청산가."""
    if trade.quantity <= 0:
        return None
    share = trade.remaining_quantity / trade.quantity
    return liquidation_price(
        direction=trade.direction,
        entry=trade.entry_price,
        quantity=trade.remaining_quantity,
        margin=trade.margin_usdt * share - funding_paid,
        mmr=mmr,
    )


def liquidation_fill(trade: Any, *, bar: Any, price: float | None) -> float | None:
    """이 봉에서 청산되나 — 되면 체결가(갭이면 시가)."""
    if price is None:
        return None
    if _direction(trade.direction) == 1:
        if bar.open <= price:
            return float(bar.open)
        return price if bar.low <= price else None
    if bar.open >= price:
        return float(bar.open)
    return price if bar.high >= price else None


def liquidation_first(trade: Any, *, bar: Any, price: float, stop_fill: float | None) -> bool:
    """B-2 — 같은 봉에서 손절도 닿으면 누가 먼저인가. 갭으로 청산가를 넘어 열렸으면 청산."""
    d = _direction(trade.direction)
    gap = bar.open <= price if d == 1 else bar.open >= price
    if gap or stop_fill is None:
        return True
    stop_closer = abs(trade.stop_price - trade.entry_price) < abs(price - trade.entry_price)
    return not stop_closer


def check_position_loss(trade: Any) -> None:
    """PART C — 가격 손익이 증거금 아래로 갈 수 없다."""
    if trade.gross_pnl_usdt < -trade.margin_usdt * (1 + 1e-9):
        raise PositionLossInvariantViolation(
            f"{trade.symbol} {trade.id}: position loss {trade.gross_pnl_usdt:.4f} exceeds margin {trade.margin_usdt:.4f}"
        )


_TIMEFRAME_MS = {"15m": 15 * 60_000, "1h": 3_600_000, "4h": 4 * 3_600_000, "1d": 24 * 3_600_000}


def with_liquidation(trade: Any, *, bar: Any) -> Any:
    """거래에 지금 청산가를 붙인다 — 단계(MMR) · 이 봉 끝까지의 펀딩으로. `evaluate_exit(liquidation_price=...)` 에 넘긴다."""
    mmr, source = maintenance_margin_rate(trade.symbol, trade.entry_price * trade.quantity)
    bar_end_ms = int(bar.timestamp.timestamp() * 1000) + _TIMEFRAME_MS.get(str(trade.timeframe), 4 * 3_600_000)
    funding = accrue_funding(trade, until_ms=bar_end_ms, price=float(bar.close))
    price = trade_liquidation(trade, mmr=mmr, funding_paid=funding["funding_paid_usdt"])
    return trade.model_copy(
        update={
            "liquidation_price": price,
            "maintenance_margin_rate": mmr,
            "mmr_source": source,
            "funding_paid_usdt": funding["funding_paid_usdt"],
        }
    )


TRACK_HALT_SYMBOL = "__track__"


def track_halt(repo: Any, track: str) -> dict[str, Any] | None:
    """트랙 정지 표식 — invariant 위반으로 멈췄나. 사람이 원인을 고치고 지울 때까지 남는다."""
    state = repo.get_paper_engine_state(TRACK_HALT_SYMBOL, track) or {}
    return state if state.get("halted") else None


def halt_track(repo: Any, track: str, *, reason: str, detail: str, at: Any) -> None:
    repo.upsert_paper_engine_state(
        TRACK_HALT_SYMBOL,
        track,
        {"halted": True, "reason": reason, "detail": detail[:500], "at": at.isoformat() if hasattr(at, "isoformat") else str(at)},
    )
    logger.error("paper track %s halted: %s — %s", track, reason, detail)
