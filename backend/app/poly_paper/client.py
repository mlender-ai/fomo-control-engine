from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any

import httpx

from .models import BookLevel, Category, OrderBook, PolyMarket


CRYPTO_TERMS = ("crypto", "bitcoin", "btc", "ethereum", "eth", "solana", "sol", "xrp")
MACRO_TERMS = (
    "macro",
    "federal reserve",
    "fed ",
    "interest rate",
    "inflation",
    "cpi",
    "gdp",
    "unemployment",
    "recession",
)
CATEGORY_TAG_SLUGS = (
    "crypto",
    "bitcoin",
    "ethereum",
    "solana",
    "xrp",
    "macro-indicators",
    "interest-rates",
    "inflation",
    "gdp",
    "unemployment",
)


class PolymarketPublicClient:
    """Public market-data surface only; authentication and trading are absent."""

    def __init__(
        self,
        *,
        gamma_base_url: str = "https://gamma-api.polymarket.com",
        clob_base_url: str = "https://clob.polymarket.com",
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.gamma_base_url = gamma_base_url.rstrip("/")
        self.clob_base_url = clob_base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport

    async def list_markets(self, *, limit: int = 100) -> list[PolyMarket]:
        requested = max(1, min(limit, 500))
        rows: dict[str, dict[str, Any]] = {}
        per_tag = max(5, min(50, requested))
        for tag_slug in CATEGORY_TAG_SLUGS:
            payload = await self._get_json(
                self.gamma_base_url,
                "/events",
                params={
                    "active": "true",
                    "closed": "false",
                    "tag_slug": tag_slug,
                    "order": "liquidity",
                    "ascending": "false",
                    "limit": per_tag,
                },
            )
            events = payload if isinstance(payload, list) else payload.get("events", []) if isinstance(payload, dict) else []
            for event in events:
                if not isinstance(event, dict):
                    continue
                for market in event.get("markets", []):
                    if not isinstance(market, dict) or bool(market.get("closed", False)) or not bool(market.get("active", True)):
                        continue
                    inherited = _inherit_event_metadata(market, event)
                    market_id = str(inherited.get("id") or inherited.get("conditionId") or "").strip()
                    if market_id:
                        rows[market_id] = inherited
        markets = [market for row in rows.values() if (market := parse_market(row)) is not None]
        markets.sort(key=lambda item: (item.trade_eligible, item.liquidity), reverse=True)
        return _balanced_categories(markets, requested)

    async def get_market(self, market_id: str) -> PolyMarket | None:
        payload = await self._get_json(self.gamma_base_url, f"/markets/{market_id}")
        return parse_market(payload) if isinstance(payload, dict) else None

    async def get_order_book(self, token_id: str) -> OrderBook:
        payload = await self._get_json(self.clob_base_url, "/book", params={"token_id": token_id})
        if not isinstance(payload, dict):
            raise ValueError("Polymarket order book response is not an object")
        observed_at = _book_time(payload.get("timestamp"))
        return OrderBook(
            token_id=str(payload.get("asset_id") or token_id),
            observed_at=observed_at,
            bids=_levels(payload.get("bids"), reverse=True),
            asks=_levels(payload.get("asks"), reverse=False),
            tick_size=_float(payload.get("tick_size")),
            last_trade_price=_float(payload.get("last_trade_price")),
        )

    async def _get_json(self, base_url: str, path: str, params: dict[str, Any] | None = None) -> Any:
        async with httpx.AsyncClient(base_url=base_url, timeout=self.timeout, transport=self.transport) as client:
            response = await client.get(path, params=params)
            response.raise_for_status()
            return response.json()


def parse_market(row: dict[str, Any]) -> PolyMarket | None:
    market_id = str(row.get("id") or row.get("conditionId") or "").strip()
    question = str(row.get("question") or row.get("title") or "").strip()
    if not market_id or not question:
        return None
    category = classify_category(row)
    if category is None:
        return None
    outcomes = _json_list(row.get("outcomes"))
    prices = _json_list(row.get("outcomePrices"))
    token_ids = _json_list(row.get("clobTokenIds"))
    outcome_map = {str(name).strip().lower(): index for index, name in enumerate(outcomes)}
    binary_yes_no = len(outcomes) == 2 and set(outcome_map) == {"yes", "no"}
    yes_index = outcome_map.get("yes")
    no_index = outcome_map.get("no")
    yes_price = _index_float(prices, yes_index)
    no_price = _index_float(prices, no_index)
    yes_token = _index_string(token_ids, yes_index)
    no_token = _index_string(token_ids, no_index)
    description = str(row.get("description") or row.get("rules") or "").strip()
    resolution_source = str(row.get("resolutionSource") or "").strip() or _rules_resolution_source(description)
    end_at = _datetime(row.get("endDate") or row.get("end_date_iso"))
    fee_schedule = row.get("feeSchedule") if isinstance(row.get("feeSchedule"), dict) else {}
    fees_enabled = bool(row.get("feesEnabled", False))
    taker_fee_rate = _float(fee_schedule.get("rate")) if fees_enabled else 0.0
    eligibility_reason = None
    if not binary_yes_no:
        eligibility_reason = "binary_yes_no_required"
    elif resolution_source is None:
        eligibility_reason = "resolution_source_missing"
    elif end_at is None:
        eligibility_reason = "resolution_time_missing"
    elif yes_token is None or no_token is None:
        eligibility_reason = "clob_tokens_missing"
    elif bool(row.get("enableOrderBook", True)) is False:
        eligibility_reason = "orderbook_disabled"
    elif fees_enabled and taker_fee_rate is None:
        eligibility_reason = "fee_schedule_missing"
    elif _ambiguous_resolution(question, description):
        eligibility_reason = "resolution_ambiguity_warning"
    return PolyMarket(
        id=market_id,
        slug=str(row.get("slug") or market_id),
        question=question,
        category=category,
        observed_at=datetime.now(timezone.utc),
        end_at=end_at,
        active=bool(row.get("active", not row.get("closed", False))),
        closed=bool(row.get("closed", False)),
        liquidity=_float(row.get("liquidityNum") or row.get("liquidity")) or 0.0,
        resolution_source=resolution_source,
        description=description,
        yes_token_id=yes_token,
        no_token_id=no_token,
        yes_price=yes_price,
        no_price=no_price,
        trade_eligible=eligibility_reason is None,
        exclusion_reason=eligibility_reason,
        taker_fee_rate=taker_fee_rate or 0.0,
        raw=row,
    )


def classify_category(row: dict[str, Any]) -> Category | None:
    values = [row.get("category"), row.get("subcategory"), row.get("question"), row.get("title")]
    tags = row.get("tags")
    if isinstance(tags, list):
        values.extend(item.get("label") if isinstance(item, dict) else item for item in tags)
    text = " ".join(str(value or "") for value in values).lower()
    if any(term in text for term in CRYPTO_TERMS):
        return Category.CRYPTO
    if any(term in text for term in MACRO_TERMS):
        return Category.MACRO
    return None


def resolved_outcome(market: PolyMarket) -> int | None:
    if not market.closed or market.yes_price is None or market.no_price is None:
        return None
    if market.yes_price >= 0.999 and market.no_price <= 0.001:
        return 1
    if market.no_price >= 0.999 and market.yes_price <= 0.001:
        return 0
    return None


def _ambiguous_resolution(question: str, description: str) -> bool:
    text = f"{question} {description}".lower()
    return any(term in text for term in ("subjective", "to be determined", "other outcome", "may resolve"))


def _inherit_event_metadata(market: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    inherited = dict(market)
    inherited["_event"] = {
        "id": event.get("id"),
        "slug": event.get("slug"),
        "title": event.get("title"),
    }
    for key in ("category", "subcategory", "tags", "resolutionSource"):
        if not inherited.get(key) and event.get(key):
            inherited[key] = event[key]
    if not inherited.get("liquidity") and event.get("liquidity"):
        inherited["liquidity"] = event["liquidity"]
    return inherited


def _rules_resolution_source(description: str) -> str | None:
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", description):
        normalized = sentence.strip()
        if "resolution source" in normalized.lower() or "resolve according to" in normalized.lower():
            return f"Market rules: {normalized[:500]}"
    return None


def _balanced_categories(markets: list[PolyMarket], limit: int) -> list[PolyMarket]:
    by_category = {category: [market for market in markets if market.category == category] for category in (Category.CRYPTO, Category.MACRO)}
    floor = max(1, limit // 3)
    selected = by_category[Category.CRYPTO][:floor] + by_category[Category.MACRO][:floor]
    selected_ids = {market.id for market in selected}
    selected.extend(market for market in markets if market.id not in selected_ids)
    return selected[:limit]


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _index_float(values: list[Any], index: int | None) -> float | None:
    return _float(values[index]) if index is not None and 0 <= index < len(values) else None


def _index_string(values: list[Any], index: int | None) -> str | None:
    if index is None or not 0 <= index < len(values):
        return None
    value = str(values[index]).strip()
    return value or None


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _book_time(value: Any) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return _datetime(value) or datetime.now(timezone.utc)
    if numeric > 10_000_000_000:
        numeric /= 1000
    return datetime.fromtimestamp(numeric, tz=timezone.utc)


def _levels(value: Any, *, reverse: bool) -> tuple[BookLevel, ...]:
    if not isinstance(value, list):
        return ()
    levels = []
    for item in value:
        if not isinstance(item, dict):
            continue
        price = _float(item.get("price"))
        size = _float(item.get("size"))
        if price is not None and size is not None and 0 < price < 1 and size > 0:
            levels.append(BookLevel(price=price, size=size))
    return tuple(sorted(levels, key=lambda item: item.price, reverse=reverse))
