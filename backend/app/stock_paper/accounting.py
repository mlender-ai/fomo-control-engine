from __future__ import annotations

from dataclasses import dataclass

from .models import Market, Side


@dataclass(frozen=True)
class FeeSchedule:
    kr_commission_rate: float = 0.00015
    us_commission_rate: float = 0.0007
    kr_sell_transaction_tax_rate: float = 0.0015


@dataclass(frozen=True)
class Fees:
    commission: float
    transaction_tax: float


def calculate_fees(market: Market, side: Side, gross_amount: float, schedule: FeeSchedule) -> Fees:
    if gross_amount < 0:
        raise ValueError("gross amount cannot be negative")
    commission_rate = schedule.kr_commission_rate if market == Market.KR else schedule.us_commission_rate
    transaction_tax = gross_amount * schedule.kr_sell_transaction_tax_rate if market == Market.KR and side == Side.SELL else 0.0
    return Fees(commission=round(gross_amount * commission_rate, 8), transaction_tax=round(transaction_tax, 8))
