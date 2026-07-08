"""거래비용 모델 (WO-FCE-36 §2).

taker 수수료 왕복 + 클래스·거래대금 구간별 슬리피지 추정.
통계는 net만 발행한다 — gross는 케이스 내부 디버그 필드로만 남는다.
"""

from __future__ import annotations

from typing import Any


def roundtrip_cost_pct(
    settings: Any,
    *,
    asset_class: str,
    quote_volume_24h: float | None = None,
) -> float:
    """왕복 비용(%) = taker 수수료 × 2 + 클래스별 슬리피지 (+ 얕은 유동성 가산)."""
    fee = float(getattr(settings, "backtest_taker_fee_pct", 0.06)) * 2
    slippage_by_class = {
        "crypto": float(getattr(settings, "backtest_slippage_crypto_pct", 0.03)),
        "stock": float(getattr(settings, "backtest_slippage_stock_pct", 0.08)),
        "index": float(getattr(settings, "backtest_slippage_index_pct", 0.05)),
    }
    slippage = slippage_by_class.get(str(asset_class), float(getattr(settings, "backtest_slippage_index_pct", 0.05)))
    shallow_floor = float(getattr(settings, "backtest_shallow_quote_volume_24h", 3_000_000.0))
    if quote_volume_24h is not None and quote_volume_24h < shallow_floor:
        slippage += float(getattr(settings, "backtest_slippage_shallow_extra_pct", 0.05))
    return round(fee + slippage, 4)
