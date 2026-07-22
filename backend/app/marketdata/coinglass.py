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

        spot_cvd = self._probe(
            "/api/spot/taker-buy-sell-volume/history",
            {"symbol": coin, "interval": self.settings.coinglass_interval, "limit": 24},
            "aggregated_spot_cvd",
        )
        futures_cvd = self._probe(
            "/api/futures/taker-buy-sell-volume/history",
            {"symbol": coin, "interval": self.settings.coinglass_interval, "limit": 24},
            "aggregated_futures_cvd",
        )
        requests_used += sum(result.get("status") != "unsupported" for result in (spot_cvd, futures_cvd))
        feature_status["aggregated_spot_cvd"] = spot_cvd["status"]
        feature_status["aggregated_futures_cvd"] = futures_cvd["status"]
        raw["aggregated_spot_cvd"] = spot_cvd.get("payload")
        raw["aggregated_futures_cvd"] = futures_cvd.get("payload")

        options = {"status": "unsupported", "payload": None}
        options_oi = {"status": "unsupported", "payload": None}
        etf_flow = {"status": "unsupported", "payload": None}
        if coin in {"BTC", "ETH"}:
            options = self._probe(
                "/api/option/put-call-ratio/history",
                {"symbol": coin, "interval": self.settings.coinglass_interval, "limit": 2},
                "options_put_call",
            )
            options_oi = self._probe(
                "/api/option/open-interest/history",
                {"symbol": coin, "interval": self.settings.coinglass_interval, "limit": 2},
                "options_open_interest",
            )
            etf_asset = "bitcoin" if coin == "BTC" else "ethereum"
            etf_flow = self._probe(
                f"/api/etf/{etf_asset}/flow-history",
                {},
                "etf_flow",
            )
            requests_used += sum(result.get("status") != "unsupported" for result in (options, options_oi, etf_flow))
        feature_status["options_put_call"] = options["status"]
        feature_status["options_open_interest"] = options_oi["status"]
        feature_status["etf_flow"] = etf_flow["status"]
        raw["options_put_call"] = options.get("payload")
        raw["options_open_interest"] = options_oi.get("payload")

        for name, result in (
            ("aggregated_oi", oi),
            ("top_trader_ls_ratio", top_ratio),
            ("oi_weighted_funding", oi_weight),
            ("liquidation_history", liq_history),
            ("liquidation_heatmap", heatmap),
        ):
            if result["status"] != "ok":
                notes.append(f"Coinglass {name} unavailable: {result.get('message') or result['status']}")

        aggregate_flow = _aggregate_money_flow(spot_cvd, futures_cvd, raw)
        if aggregate_flow is not None:
            raw["money_flow_aggregate"] = aggregate_flow
        raw["options_summary"] = _options_summary(options, options_oi, coin)
        if coin in {"BTC", "ETH"}:
            raw["etf_flow_summary"] = _etf_flow_summary(etf_flow, coin)
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

    def _probe(self, path: str, params: dict[str, Any], feature: str) -> dict[str, Any]:
        try:
            return self._get(path, params, feature)
        except (KeyError, NotImplementedError) as exc:
            # Optional Tier 2 probes must not disable the core derivative feed.
            return {"status": "unsupported", "message": str(exc), "payload": None, "feature": feature}


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


def _aggregate_money_flow(spot: dict[str, Any], futures: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any] | None:
    spot_rows = _payload_rows(spot.get("payload")) if spot.get("status") == "ok" else []
    futures_rows = _payload_rows(futures.get("payload")) if futures.get("status") == "ok" else []
    spot_ratio = _cvd_ratio(spot_rows)
    futures_ratio = _cvd_ratio(futures_rows)
    if spot_ratio is None or futures_ratio is None:
        return None
    latest_time = _row_time(spot_rows[-1]) if spot_rows else None
    return {
        "as_of": (latest_time or utc_now()).isoformat(),
        "source": "coinglass_agg",
        "spot_source": "coinglass_agg",
        "futures_source": "coinglass_agg",
        "spot_cvd_delta_ratio": spot_ratio,
        "futures_cvd_delta_ratio": futures_ratio,
        "price_change_pct": _latest_numeric(raw.get("aggregated_oi"), ("price_change_percent_24h", "priceChangePercent24h")),
        "oi_change_pct": _latest_numeric(raw.get("aggregated_oi"), ("open_interest_change_percent_24h", "openInterestChangePercent24h")),
        "spot_cvd": _cvd_points(spot_rows),
        "futures_cvd": _cvd_points(futures_rows),
        "coverage": {"spot_available": True, "futures_available": True, "aggregation": "all_exchanges", "window_buckets": len(spot_rows)},
        "notes": [],
    }


def _options_summary(result: dict[str, Any], oi_result: dict[str, Any], coin: str) -> dict[str, Any]:
    if coin not in {"BTC", "ETH"}:
        return {"available": False, "status": "unsupported_symbol", "reason": "옵션 데이터는 BTC/ETH만 지원합니다."}
    if result.get("status") != "ok":
        return {"available": False, "status": result.get("status", "locked"), "reason": "Coinglass 옵션 기능이 잠겨 있거나 플랜에 포함되지 않았습니다."}
    rows = _payload_rows(result.get("payload"))
    oi_rows = _payload_rows(oi_result.get("payload"))
    latest = rows[-1] if rows else {}
    return {
        "available": bool(latest),
        "status": "ok" if latest else "empty",
        "put_call_ratio": _first_number(latest, ("put_call_ratio", "putCallRatio", "close")),
        "options_open_interest": _first_number(oi_rows[-1], ("open_interest", "openInterest", "oi")) if oi_rows else None,
        "source": "coinglass_agg",
        "as_of": (_row_time(latest) or utc_now()).isoformat() if latest else None,
    }


def _etf_flow_summary(result: dict[str, Any], coin: str) -> dict[str, Any]:
    """Normalize CoinGlass daily spot-ETF reports without inventing missing values."""
    if coin not in {"BTC", "ETH"}:
        return {"available": False, "status": "unsupported_symbol"}
    status = str(result.get("status") or "error")
    if status != "ok":
        reason = "CoinGlass API 키 또는 플랜에 ETF flow 접근 권한이 없습니다." if status == "locked" else "CoinGlass ETF flow 수집에 실패했습니다."
        return {
            "asset": coin,
            "available": False,
            "status": status,
            "source": "coinglass_v4",
            "reason": reason,
            "provider_message": result.get("message"),
        }
    rows = sorted(
        _payload_rows(result.get("payload")),
        key=lambda row: _row_time(row) or datetime.min.replace(tzinfo=timezone.utc),
    )
    rows = [row for row in rows if _first_number(row, ("flow_usd", "flowUsd")) is not None]
    if not rows:
        return {
            "asset": coin,
            "available": False,
            "status": "empty",
            "source": "coinglass_v4",
            "reason": "CoinGlass ETF flow 응답에 보고된 값이 없습니다.",
        }
    latest = rows[-1]
    latest_flow = _first_number(latest, ("flow_usd", "flowUsd"))
    recent_values = [value for row in rows[-5:] if (value := _first_number(row, ("flow_usd", "flowUsd"))) is not None]
    detail = latest.get("etf_flows") or latest.get("etfFlows")
    contributors: list[dict[str, Any]] = []
    if isinstance(detail, list):
        for item in detail:
            if not isinstance(item, dict):
                continue
            value = _first_number(item, ("flow_usd", "flowUsd"))
            ticker = item.get("etf_ticker") or item.get("etfTicker") or item.get("ticker")
            if value is None or not ticker:
                continue
            contributors.append({"ticker": str(ticker).upper(), "flow_usd": value})
    contributors.sort(key=lambda item: abs(float(item["flow_usd"])), reverse=True)
    as_of = _row_time(latest)
    return {
        "asset": coin,
        "available": latest_flow is not None,
        "status": "ok",
        "source": "coinglass_v4",
        "source_label": "CoinGlass 미국 현물 ETF 집계",
        "as_of": as_of.isoformat() if as_of else None,
        "daily_flow_usd": latest_flow,
        "five_report_day_flow_usd": sum(recent_values) if recent_values else None,
        "report_days": len(recent_values),
        "price_usd": _first_number(latest, ("price_usd", "priceUsd")),
        "contributors": contributors[:5],
        "cadence": "daily",
        "truth_label": "일별 ETF 보고 · 실시간 체결 아님",
    }


def _payload_rows(payload: Any) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("data_list", "list", "rows"):
            if isinstance(data.get(key), list):
                return [item for item in data[key] if isinstance(item, dict)]
    return []


def _cvd_ratio(rows: list[dict[str, Any]]) -> float | None:
    buy = sum(_first_number(row, ("buy_volume", "buyVolume", "taker_buy_volume", "takerBuyVolume")) or 0 for row in rows)
    sell = sum(_first_number(row, ("sell_volume", "sellVolume", "taker_sell_volume", "takerSellVolume")) or 0 for row in rows)
    return round((buy - sell) / (buy + sell), 8) if buy + sell > 0 else None


def _cvd_points(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = 0.0
    result: list[dict[str, Any]] = []
    for row in rows:
        buy = _first_number(row, ("buy_volume", "buyVolume", "taker_buy_volume", "takerBuyVolume")) or 0
        sell = _first_number(row, ("sell_volume", "sellVolume", "taker_sell_volume", "takerSellVolume")) or 0
        total += buy - sell
        result.append({"time": (_row_time(row) or utc_now()).isoformat(), "value": round(total, 8)})
    return result


def _latest_numeric(payload: Any, keys: tuple[str, ...]) -> float | None:
    rows = _payload_rows(payload)
    return _first_number(rows[-1], keys) if rows else None


def _first_number(row: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _optional_float(row.get(key))
        if value is not None:
            return value
    return None


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
    return _timestamp_ms(row.get("time") if row.get("time") is not None else row.get("timestamp"))


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
