from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import httpx

from app.core.config import Settings
from app.db.models import (
    DerivativeDataSnapshot,
    DerivativeMetric,
    LiquidationEvent,
    utc_now,
)
from app.marketdata.base import DerivativeCollection, coin_from_symbol

logger = logging.getLogger(__name__)


class CoinglassProvider:
    source = "coinglass"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.coinglass_base_url.rstrip("/")
        self.api_key = settings.coinglass_api_key.strip()

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def collect(self, symbol: str) -> DerivativeCollection:
        normalized = symbol.upper()
        if not self.configured:
            metric = DerivativeMetric(
                symbol=normalized,
                source="coinglass",
                tier="coinglass",
                source_status="locked",
                data_quality={"source": "coinglass_v4", "configured": False},
                notes=["Coinglass V4 key is not configured. Tier 2 OI, liquidation, top trader ratio are locked."],
                as_of=utc_now(),
            )
            snapshot = _snapshot_from_metric(metric)
            return DerivativeCollection(
                provider="coinglass",
                symbol=normalized,
                metrics=[metric],
                snapshot=snapshot,
                feature_status={"configured": False},
            )

        coin = coin_from_symbol(normalized)
        feature_status: dict[str, Any] = {"configured": True}
        raw: dict[str, Any] = {}
        notes: list[str] = []
        requests_used = 0

        subscription = self._get("/api/user/account/subscription", {}, "subscription")
        requests_used += 1
        feature_status["subscription"] = subscription["status"]
        raw["subscription"] = subscription.get("payload")

        oi = self._get(
            "/api/futures/open-interest/exchange-list",
            {"symbol": coin},
            "aggregated_oi",
        )
        requests_used += 1
        feature_status["aggregated_oi"] = oi["status"]
        raw["aggregated_oi"] = oi.get("payload")

        top_ratio = self._get(
            "/api/futures/top-long-short-account-ratio/history",
            {
                "exchange": self.settings.coinglass_top_ratio_exchange,
                "symbol": normalized,
                "interval": self.settings.coinglass_interval,
                "limit": 2,
            },
            "top_trader_ls_ratio",
        )
        requests_used += 1
        feature_status["top_trader_ls_ratio"] = top_ratio["status"]
        raw["top_trader_ls_ratio"] = top_ratio.get("payload")

        oi_weight = self._get(
            "/api/futures/funding-rate/oi-weight-history",
            {"symbol": coin, "interval": self.settings.coinglass_interval, "limit": 2},
            "oi_weighted_funding",
        )
        requests_used += 1
        feature_status["oi_weighted_funding"] = oi_weight["status"]
        raw["oi_weighted_funding"] = oi_weight.get("payload")

        liq_history = self._get(
            "/api/futures/liquidation/aggregated-history",
            {
                "exchange_list": self.settings.coinglass_exchange_list,
                "symbol": coin,
                "interval": self.settings.coinglass_liquidation_interval,
                "limit": 24,
            },
            "liquidation_history",
        )
        requests_used += 1
        feature_status["liquidation_history"] = liq_history["status"]
        raw["liquidation_history"] = liq_history.get("payload")

        heatmap = self._get(
            "/api/futures/liquidation/aggregated-heatmap/model2",
            {"symbol": coin, "range": self.settings.coinglass_heatmap_range},
            "liquidation_heatmap",
        )
        requests_used += 1
        feature_status["liquidation_heatmap"] = heatmap["status"]
        raw["liquidation_heatmap"] = heatmap.get("payload")

        for name, result in (
            ("aggregated_oi", oi),
            ("top_trader_ls_ratio", top_ratio),
            ("oi_weighted_funding", oi_weight),
            ("liquidation_history", liq_history),
            ("liquidation_heatmap", heatmap),
        ):
            if result["status"] != "ok":
                notes.append(f"Coinglass {name} unavailable: {result.get('message') or result['status']}")

        metric = _metric_from_results(normalized, oi, top_ratio, oi_weight, feature_status, notes, raw)
        events = _liquidation_events_from_result(normalized, liq_history, self.settings.coinglass_liquidation_interval)
        clusters = _clusters_from_heatmap(heatmap)
        snapshot = _snapshot_from_metric(metric).model_copy(update={"liquidation_clusters": clusters, "raw_json": raw})
        return DerivativeCollection(
            provider="coinglass",
            symbol=normalized,
            metrics=[metric],
            liquidation_events=events,
            snapshot=snapshot,
            feature_status=feature_status,
            requests_used=requests_used,
            errors=notes,
        )

    def _get(self, path: str, params: dict[str, Any], feature: str) -> dict[str, Any]:
        headers = {"CG-API-KEY": self.api_key}
        try:
            with httpx.Client(base_url=self.base_url, timeout=10.0, headers=headers) as client:
                response = client.get(
                    path,
                    params={key: value for key, value in params.items() if value not in (None, "")},
                )
        except httpx.HTTPError as exc:
            return {
                "status": "error",
                "message": f"network_error: {exc}",
                "payload": None,
            }
        if response.status_code in {401, 403}:
            return {
                "status": "locked",
                "message": f"http_{response.status_code}",
                "payload": _safe_json(response),
            }
        if response.status_code == 429:
            return {
                "status": "error",
                "message": "rate_limited",
                "payload": _safe_json(response),
            }
        if response.status_code >= 400:
            return {
                "status": "error",
                "message": f"http_{response.status_code}",
                "payload": _safe_json(response),
            }
        payload = _safe_json(response)
        code = str(payload.get("code", "0")) if isinstance(payload, dict) else "invalid"
        if code != "0":
            if code in {"401", "403"}:
                return {
                    "status": "locked",
                    "message": str(payload.get("msg") or code),
                    "payload": payload,
                }
            return {
                "status": "error",
                "message": str(payload.get("msg") or code),
                "payload": payload,
            }
        return {"status": "ok", "message": "ok", "payload": payload, "feature": feature}


def _metric_from_results(
    symbol: str,
    oi: dict[str, Any],
    top_ratio: dict[str, Any],
    oi_weight: dict[str, Any],
    feature_status: dict[str, Any],
    notes: list[str],
    raw: dict[str, Any],
) -> DerivativeMetric:
    oi_row = _aggregated_oi_row(oi.get("payload"))
    top_row = _latest_row(top_ratio.get("payload"))
    oi_weight_row = _latest_row(oi_weight.get("payload"))
    ok_features = sum(1 for key in ("aggregated_oi", "top_trader_ls_ratio", "oi_weighted_funding") if feature_status.get(key) == "ok")
    return DerivativeMetric(
        symbol=symbol,
        source="coinglass",
        tier="coinglass",
        as_of=_row_time(top_row) or _row_time(oi_weight_row) or utc_now(),
        open_interest=_optional_float(oi_row.get("open_interest_quantity") if oi_row else None),
        open_interest_value=_optional_float(oi_row.get("open_interest_usd") if oi_row else None),
        oi_change_pct=_optional_float(oi_row.get("open_interest_change_percent_24h") if oi_row else None),
        top_ls=_optional_float(top_row.get("top_account_long_short_ratio") if top_row else None),
        oi_weighted_funding=_optional_float(oi_weight_row.get("close") if oi_weight_row else None),
        source_status="ok" if ok_features == 3 else "partial" if ok_features else "error",
        data_quality={"source": "coinglass_v4", "features": feature_status},
        coverage={
            "aggregation": "exchange_all",
            "top_ratio_exchange": "configured",
            "interval": "configured",
        },
        notes=notes,
        raw_json=raw,
    )


def _snapshot_from_metric(metric: DerivativeMetric) -> DerivativeDataSnapshot:
    return DerivativeDataSnapshot(
        symbol=metric.symbol,
        provider=metric.source,
        tier=metric.tier,
        as_of=metric.as_of,
        open_interest=metric.open_interest,
        open_interest_value=metric.open_interest_value,
        open_interest_change_pct=metric.oi_change_pct,
        funding_rate=metric.funding,
        next_funding_time=metric.funding_next,
        long_short_ratio=metric.taker_ls,
        long_account_ratio=metric.long_account_ratio,
        short_account_ratio=metric.short_account_ratio,
        top_long_short_ratio=metric.top_ls,
        oi_weighted_funding_rate=metric.oi_weighted_funding,
        data_quality=metric.data_quality,
        source_status=metric.source_status,
        notes=metric.notes,
        raw_json=metric.raw_json,
        created_at=metric.created_at,
    )


def _liquidation_events_from_result(symbol: str, result: dict[str, Any], interval: str) -> list[LiquidationEvent]:
    if result.get("status") != "ok":
        return []
    payload = result.get("payload")
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    events: list[LiquidationEvent] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        bucket_start = _timestamp_ms(row.get("time"))
        if bucket_start is None:
            continue
        events.append(
            LiquidationEvent(
                id=uuid5(
                    NAMESPACE_URL,
                    f"fce:liquidation:{symbol}:coinglass:{interval}:{bucket_start.isoformat()}",
                ),
                symbol=symbol,
                source="coinglass",
                interval=interval,
                bucket_start=bucket_start,
                long_liquidation_usd=_optional_float(row.get("aggregated_long_liquidation_usd")) or 0.0,
                short_liquidation_usd=_optional_float(row.get("aggregated_short_liquidation_usd")) or 0.0,
                data_quality={
                    "source": "coinglass_v4",
                    "endpoint": "aggregated-liquidation-history",
                },
                raw_json=row,
            )
        )
    return events


def _clusters_from_heatmap(result: dict[str, Any]) -> list[dict[str, Any]]:
    if result.get("status") != "ok":
        return []
    payload = result.get("payload")
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return []
    y_axis = data.get("y_axis")
    leverage_data = data.get("liquidation_leverage_data")
    if not isinstance(y_axis, list) or not isinstance(leverage_data, list):
        return []
    amount_by_index: dict[int, float] = {}
    for row in leverage_data:
        if not isinstance(row, list) or len(row) < 3:
            continue
        try:
            y_index = int(row[1])
            amount_by_index[y_index] = amount_by_index.get(y_index, 0.0) + float(row[2])
        except (TypeError, ValueError):
            continue
    if not amount_by_index:
        return []
    current = _latest_heatmap_close(data)
    max_amount = max(amount_by_index.values())
    clusters: list[dict[str, Any]] = []
    for y_index, amount in sorted(amount_by_index.items(), key=lambda item: item[1], reverse=True)[:6]:
        if y_index < 0 or y_index >= len(y_axis):
            continue
        price = _optional_float(y_axis[y_index])
        if price is None:
            continue
        kind = "resistance" if current is not None and price > current else "support" if current is not None else "liquidation"
        clusters.append(
            {
                "price": price,
                "score": round((amount / max_amount) * 100, 2) if max_amount > 0 else 0,
                "touches": 0,
                "kind": kind,
                "sources": ["liq_cluster"],
                "amount_usd": amount,
                "source": "coinglass",
                "method": "coinglass_heatmap_model2",
            }
        )
    return clusters


def _aggregated_oi_row(payload: Any) -> dict[str, Any] | None:
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict) and str(row.get("exchange", "")).lower() == "all":
            return row
    return next((row for row in rows if isinstance(row, dict)), None)


def _latest_row(payload: Any) -> dict[str, Any] | None:
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        return None
    dict_rows = [row for row in rows if isinstance(row, dict)]
    if not dict_rows:
        return None
    return sorted(dict_rows, key=lambda row: _optional_float(row.get("time")) or 0)[-1]


def _row_time(row: dict[str, Any] | None) -> datetime | None:
    if not row:
        return None
    return _timestamp_ms(row.get("time"))


def _latest_heatmap_close(data: dict[str, Any]) -> float | None:
    candles = data.get("price_candlesticks")
    if not isinstance(candles, list) or not candles:
        return None
    latest = candles[-1]
    if not isinstance(latest, list) or len(latest) < 5:
        return None
    return _optional_float(latest[4])


def _timestamp_ms(value: Any) -> datetime | None:
    numeric = _optional_float(value)
    if numeric is None:
        return None
    if numeric > 10_000_000_000:
        numeric = numeric / 1000
    return datetime.fromtimestamp(numeric, timezone.utc)


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {"raw_text": response.text[:500]}
    return payload if isinstance(payload, dict) else {"data": payload}
