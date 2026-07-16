from __future__ import annotations

import csv
import io
import threading
import time
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.marketdata.assets import base_ticker


OCC_BASE_URL = "https://marketdata.theocc.com"
SUPPORTED_ASSET_CLASSES = {"stock", "index"}

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()


def occ_options_for_analysis(symbol: str, asset_class: str, settings: Any) -> dict[str, Any] | None:
    """Return an observation-only OCC summary for US equity/index underlyings."""
    if asset_class not in SUPPORTED_ASSET_CLASSES:
        return None
    underlying = base_ticker(symbol)
    if not bool(getattr(settings, "occ_options_enabled", True)):
        return _unavailable(underlying, "disabled", "OCC 옵션 관측이 비활성화되어 있습니다.")
    if bool(getattr(settings, "demo_mode", False)):
        return _unavailable(underlying, "demo", "데모 모드에서는 실시간 OCC 조회를 생략합니다.")
    if str(getattr(settings, "market_data_provider", "mock")).lower() != "bitget":
        return _unavailable(underlying, "provider_disabled", "실데이터 모드에서 OCC 옵션을 조회합니다.")

    ttl = max(60, int(getattr(settings, "occ_options_cache_ttl_seconds", 1800)))
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(underlying)
        if cached and now - cached[0] < ttl:
            return cached[1]

    try:
        summary = fetch_occ_options_summary(
            underlying,
            timeout=float(getattr(settings, "occ_options_timeout_seconds", 10.0)),
        )
    except (httpx.HTTPError, ValueError) as exc:
        summary = _unavailable(underlying, "error", f"OCC 조회 실패: {type(exc).__name__}")
    with _CACHE_LOCK:
        _CACHE[underlying] = (now, summary)
    return summary


def fetch_occ_options_summary(
    underlying: str,
    *,
    timeout: float = 10.0,
    client: httpx.Client | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    normalized = base_ticker(underlying)
    current = now or datetime.now(timezone.utc)
    eastern_today = current.astimezone(ZoneInfo("America/New_York")).date()
    manager = nullcontext(client) if client is not None else httpx.Client(timeout=timeout, headers={"User-Agent": "FOMO-Control-Engine/1.0"})
    with manager as http:
        assert http is not None
        series_response = http.get(
            f"{OCC_BASE_URL}/series-search",
            params={"symbolType": "U", "symbol": normalized},
        )
        series_response.raise_for_status()
        series = parse_series_search(series_response.text, normalized, as_of=eastern_today)
        if not series:
            raise ValueError("OCC series search returned no exact-symbol contracts")

        volume: dict[str, Any] | None = None
        for report_date in _previous_business_dates(eastern_today, limit=5):
            response = http.get(
                f"{OCC_BASE_URL}/volume-query",
                params={
                    "reportDate": report_date.strftime("%Y%m%d"),
                    "format": "csv",
                    "volumeQueryType": "O",
                    "symbolType": "U",
                    "symbol": normalized,
                    "reportType": "D",
                    "accountType": "ALL",
                    "porc": "BOTH",
                },
            )
            response.raise_for_status()
            volume = parse_volume_query(response.text, normalized)
            if volume is not None:
                break

    call_oi = sum(item["call_open_interest"] for item in series)
    put_oi = sum(item["put_open_interest"] for item in series)
    max_pain = calculate_nearest_expiry_max_pain(series, as_of=eastern_today)
    top_calls = _top_contracts(series, "call_open_interest")
    top_puts = _top_contracts(series, "put_open_interest")
    call_volume = volume["call_volume"] if volume else None
    put_volume = volume["put_volume"] if volume else None
    notes = [
        "OI는 OCC 전일 결제 기준이며 장중 실시간 수치가 아닙니다.",
        "옵션 포지셔닝 관측 전용이며 방향 판정과 자동 진입에 사용하지 않습니다.",
    ]
    if volume is None:
        notes.append("최근 완료 거래일의 OCC 계약량을 찾지 못했습니다.")
    return {
        "available": True,
        "status": "ok",
        "source": "occ_public",
        "source_label": "OCC 공식 무키 데이터",
        "underlying": normalized,
        "as_of": current.isoformat(),
        "open_interest_basis": "previous_settlement",
        "call_open_interest": call_oi,
        "put_open_interest": put_oi,
        "put_call_oi_ratio": _ratio(put_oi, call_oi),
        "call_volume": call_volume,
        "put_volume": put_volume,
        "put_call_volume_ratio": _ratio(put_volume, call_volume),
        "volume_date": volume["volume_date"] if volume else None,
        **max_pain,
        "contract_count": len(series),
        "top_call_contracts": top_calls,
        "top_put_contracts": top_puts,
        "notes": notes,
    }


def calculate_nearest_expiry_max_pain(
    series: list[dict[str, Any]],
    *,
    as_of: date,
) -> dict[str, Any]:
    """Calculate observation-only max pain for the nearest OCC expiry."""
    expiries = sorted({str(item["expiry"]) for item in series if str(item.get("expiry") or "") >= as_of.isoformat()})
    if not expiries:
        return {
            "max_pain_price": None,
            "max_pain_expiry": None,
            "days_to_expiry": None,
            "max_pain_basis": "nearest_expiry_open_interest",
        }

    expiry = expiries[0]
    by_strike: dict[float, dict[str, int]] = {}
    for item in series:
        if item.get("expiry") != expiry:
            continue
        strike = float(item["strike"])
        bucket = by_strike.setdefault(strike, {"call": 0, "put": 0})
        bucket["call"] += int(item["call_open_interest"])
        bucket["put"] += int(item["put_open_interest"])

    total_oi = sum(values["call"] + values["put"] for values in by_strike.values())
    weighted_center = sum(strike * (values["call"] + values["put"]) for strike, values in by_strike.items()) / total_oi if total_oi else 0.0

    def settlement_cost(settlement: float) -> float:
        return sum(values["call"] * max(settlement - strike, 0.0) + values["put"] * max(strike - settlement, 0.0) for strike, values in by_strike.items())

    max_pain_price = min(
        by_strike,
        key=lambda strike: (round(settlement_cost(strike), 8), abs(strike - weighted_center), strike),
    )
    expiry_date = date.fromisoformat(expiry)
    return {
        "max_pain_price": max_pain_price,
        "max_pain_expiry": expiry,
        "days_to_expiry": (expiry_date - as_of).days,
        "max_pain_basis": "nearest_expiry_open_interest",
    }


def parse_series_search(text: str, underlying: str, *, as_of: date | None = None) -> list[dict[str, Any]]:
    normalized = base_ticker(underlying)
    cutoff = as_of or datetime.now(timezone.utc).date()
    contracts: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        parts = [part.strip() for part in raw_line.split("\t") if part.strip()]
        if len(parts) < 10 or parts[0].upper() != normalized:
            continue
        try:
            expiry = date(int(parts[1]), int(parts[2]), int(parts[3]))
            strike = float(f"{int(parts[4])}.{parts[5]}")
            call_oi = int(parts[7].replace(",", ""))
            put_oi = int(parts[8].replace(",", ""))
        except (ValueError, IndexError):
            continue
        if expiry < cutoff:
            continue
        contracts.append(
            {
                "expiry": expiry.isoformat(),
                "strike": strike,
                "call_open_interest": call_oi,
                "put_open_interest": put_oi,
            }
        )
    return contracts


def parse_volume_query(text: str, underlying: str) -> dict[str, Any] | None:
    normalized = base_ticker(underlying)
    if "No record(s) found" in text:
        return None
    call_volume = 0
    put_volume = 0
    dates: set[str] = set()
    matched = False
    for row in csv.DictReader(io.StringIO(text)):
        if str(row.get("underlying") or "").strip().upper() != normalized:
            continue
        if str(row.get("symbol") or "").strip().upper() != normalized:
            continue
        try:
            quantity = int(str(row.get("quantity") or "0").replace(",", ""))
        except ValueError:
            continue
        side = str(row.get("porc") or "").strip().upper()
        if side == "C":
            call_volume += quantity
        elif side == "P":
            put_volume += quantity
        else:
            continue
        matched = True
        raw_date = str(row.get("actdate") or "").strip()
        try:
            dates.add(datetime.strptime(raw_date, "%m/%d/%Y").date().isoformat())
        except ValueError:
            if len(raw_date) == 8 and raw_date.isdigit():
                dates.add(f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}")
    if not matched:
        return None
    return {
        "call_volume": call_volume,
        "put_volume": put_volume,
        "volume_date": max(dates) if dates else None,
    }


def _previous_business_dates(today: date, *, limit: int) -> list[date]:
    dates: list[date] = []
    cursor = today - timedelta(days=1)
    while len(dates) < limit:
        if cursor.weekday() < 5:
            dates.append(cursor)
        cursor -= timedelta(days=1)
    return dates


def _top_contracts(series: list[dict[str, Any]], field: str, *, limit: int = 3) -> list[dict[str, Any]]:
    return [
        {"expiry": item["expiry"], "strike": item["strike"], "open_interest": item[field]}
        for item in sorted(series, key=lambda item: item[field], reverse=True)[:limit]
        if item[field] > 0
    ]


def _ratio(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return round(numerator / denominator, 4)


def _unavailable(underlying: str, status: str, note: str) -> dict[str, Any]:
    return {
        "available": False,
        "status": status,
        "source": "occ_public",
        "source_label": "OCC 공식 무키 데이터",
        "underlying": underlying,
        "notes": [note],
    }
