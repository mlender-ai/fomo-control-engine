from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any

from app.db.models import MarketSnapshot
from app.exchange.errors import MarketDataError
from app.positions.chart_analysis import build_chart_analysis


PATTERN_MATRIX_TIMEFRAMES = ("1d", "12h", "4h", "1h", "15m")


def build_pattern_matrix(
    symbol: str,
    snapshot_loader: Callable[[str, str], MarketSnapshot],
    *,
    timeframes: Iterable[str] = PATTERN_MATRIX_TIMEFRAMES,
) -> dict[str, Any]:
    """Analyze existing pattern engines on independent confirmed timeframes.

    One unavailable timeframe is represented as data, not raised as a matrix
    failure. Programming errors still propagate; only expected market-data and
    insufficient-candle failures are isolated.
    """

    rows = []
    for timeframe in timeframes:
        try:
            analysis = build_chart_analysis(snapshot_loader(symbol, timeframe))
        except (MarketDataError, ValueError) as exc:
            rows.append(
                {
                    "timeframe": timeframe,
                    "status": "unavailable",
                    "reason": str(exc),
                    "candles": 0,
                    "last_confirmed_at": None,
                    "wyckoff": _empty_wyckoff(),
                    "harmonic": _empty_harmonic(),
                }
            )
            continue
        rows.append(_matrix_row(timeframe, analysis))

    found = [row["timeframe"] for row in rows if row["status"] == "ok" and (row["wyckoff"]["detected"] or row["harmonic"]["detected"])]
    return {
        "symbol": symbol.upper(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy": "independent_timeframes_confirmed_candles_only",
        "timeframes": rows,
        "found_timeframes": found,
    }


def _matrix_row(timeframe: str, analysis: dict[str, Any]) -> dict[str, Any]:
    markers = list(analysis.get("wyckoff_markers") or [])
    phase = analysis.get("wyckoff_phase") if isinstance(analysis.get("wyckoff_phase"), dict) else {}
    harmonic_patterns = list(analysis.get("harmonic_patterns") or [])
    best_harmonic = max(harmonic_patterns, key=lambda item: float(item.get("confidence") or 0), default=None)
    data_quality = analysis.get("data_quality") if isinstance(analysis.get("data_quality"), dict) else {}
    wyckoff_range = analysis.get("wyckoff_range")
    return {
        "timeframe": timeframe,
        "status": "ok",
        "reason": None,
        "candles": int(data_quality.get("analysis_candles") or 0),
        "last_confirmed_at": _json_time(data_quality.get("last_candle_at")),
        "wyckoff": {
            "detected": bool(wyckoff_range or markers),
            "range_detected": bool(wyckoff_range),
            "phase": str(phase.get("phase") or "undetermined"),
            "side": str(phase.get("side") or "neutral"),
            "event_count": len(markers),
            "events": [
                {
                    "label": marker.get("label"),
                    "type": marker.get("type"),
                    "confidence": marker.get("confidence"),
                    "time": marker.get("time"),
                }
                for marker in markers[-4:]
                if isinstance(marker, dict)
            ],
        },
        "harmonic": {
            "detected": bool(harmonic_patterns),
            "pattern_count": len(harmonic_patterns),
            "best_pattern": (
                {
                    "label": best_harmonic.get("label"),
                    "direction": best_harmonic.get("direction"),
                    "status": best_harmonic.get("status"),
                    "confidence": best_harmonic.get("confidence"),
                }
                if isinstance(best_harmonic, dict)
                else None
            ),
        },
    }


def _empty_wyckoff() -> dict[str, Any]:
    return {
        "detected": False,
        "range_detected": False,
        "phase": "unavailable",
        "side": "neutral",
        "event_count": 0,
        "events": [],
    }


def _empty_harmonic() -> dict[str, Any]:
    return {"detected": False, "pattern_count": 0, "best_pattern": None}


def _json_time(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value is not None else None
