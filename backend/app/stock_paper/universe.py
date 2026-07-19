from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.toss.signals import warning_gate

from .models import Currency, Market


DEFAULT_UNIVERSE_PATH = Path(__file__).with_name("universe") / "2026-q3.json"


@dataclass(frozen=True)
class StockInstrument:
    symbol: str
    market: Market
    currency: Currency
    index: str
    tick_rule: str
    universe_version: str
    active_for_entry: bool = True


@dataclass(frozen=True)
class StockUniverse:
    version: str
    effective_at: str
    instruments: tuple[StockInstrument, ...]
    sources: dict[str, dict[str, str]]

    def for_market(self, market: Market) -> tuple[StockInstrument, ...]:
        return tuple(item for item in self.instruments if item.market == market)

    def entry_allowed(self, market: Market, symbol: str, warnings: list[str] | tuple[str, ...]) -> tuple[bool, str | None]:
        match = next((item for item in self.instruments if item.market == market and item.symbol == symbol.upper()), None)
        if match is None or not match.active_for_entry:
            return False, "universe_entry_blocked"
        excluded, badges = warning_gate(warnings)
        if excluded:
            return False, "warning_hard_gate"
        if any(item.startswith("vi") or item == "변동성완화장치" for item in badges):
            return False, "vi"
        return True, None


def load_universe(path: Path = DEFAULT_UNIVERSE_PATH) -> StockUniverse:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    instruments: list[StockInstrument] = []
    sources: dict[str, dict[str, str]] = {}
    for market_name, config in payload["markets"].items():
        market = Market(market_name)
        symbols = [str(value).strip().upper() for value in config["symbols"]]
        if len(symbols) != 100 or len(set(symbols)) != 100:
            raise ValueError(f"{market.value} universe must contain 100 unique symbols")
        sources[market.value] = {"source": str(config["source"]), "source_as_of": str(config["source_as_of"])}
        instruments.extend(
            StockInstrument(
                symbol=symbol,
                market=market,
                currency=Currency(str(config["currency"])),
                index=str(config["index"]),
                tick_rule=str(config["tick_rule"]),
                universe_version=str(payload["version"]),
            )
            for symbol in symbols
        )
    if len(instruments) != 200:
        raise ValueError("stock paper universe must contain exactly 200 instruments")
    return StockUniverse(
        version=str(payload["version"]),
        effective_at=str(payload["effective_at"]),
        instruments=tuple(instruments),
        sources=sources,
    )
