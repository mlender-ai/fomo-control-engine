from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any

from app.analyst.confluence import TIMEFRAME_MINUTES, build_confluence
from app.db.models import MarketCandle, MarketSnapshot
from app.positions.chart_analysis import MIN_CHART_CANDLES, build_chart_analysis


HISTORY_WINDOW = 72
_CACHE: dict[tuple[str, str], dict[str, Any]] = {}
_CACHE_LOCK = RLock()


def replay_stance_history(
    *,
    analysis: dict[str, Any],
    current_confluence: dict[str, Any],
    bar_state: dict[str, Any],
    timeframe: str,
    hysteresis_params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Replay the live stance state machine over confirmed candle prefixes.

    Each step rebuilds chart evidence using only candles available at that
    point, then calls ``build_confluence`` with the previous in-memory state.
    The current live point is replaced by the already persisted live state so
    the ribbon endpoint and the application verdict are byte-consistent.
    """

    candles = _candles(analysis)
    if bar_state.get("provisional") is True and candles:
        candles = candles[:-1]
    if len(candles) < MIN_CHART_CANDLES:
        return _live_only(current_confluence, candles)

    symbol = str(analysis.get("symbol") or "").upper()
    key = (symbol, timeframe)
    last_time = candles[-1].timestamp
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and cached.get("last_time") == last_time:
            cached["prior_state"] = _live_state(current_confluence) or cached.get("prior_state")
            return _with_live_endpoint(list(cached["history"]), current_confluence, last_time)

        if cached and _can_increment(cached, candles):
            prior = cached.get("prior_state")
            point = _replay_point(symbol, timeframe, candles, prior, hysteresis_params)
            history = [*cached["history"], point][-HISTORY_WINDOW:]
        else:
            history, prior = _full_replay(symbol, timeframe, candles, hysteresis_params)
            point = history[-1] if history else None
            if point is not None:
                prior = point.get("state")

        _CACHE[key] = {
            "last_time": last_time,
            "history": history,
            # The next confirmed candle must continue from the persisted live
            # state, not from the reconstructed point that the UI replaces.
            "prior_state": _live_state(current_confluence) or prior,
        }
        return _with_live_endpoint(list(history), current_confluence, last_time)


def clear_stance_history_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def replay_confirmed_stance_points(
    *,
    symbol: str,
    timeframe: str,
    candles: list[MarketCandle],
    hysteresis_params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Replay every confirmed prefix for historical validation.

    Unlike the UI helper, this intentionally returns the full replay so the
    scoring harness can advance hysteresis on every bar before selecting
    non-overlapping outcome anchors. No future candle is passed to a point.
    """

    ordered = sorted(candles, key=lambda candle: candle.timestamp)
    history, _prior = _full_replay(
        symbol.upper(),
        timeframe,
        ordered,
        hysteresis_params,
        history_limit=None,
    )
    return history


def _full_replay(
    symbol: str,
    timeframe: str,
    candles: list[MarketCandle],
    hysteresis_params: dict[str, Any] | None,
    history_limit: int | None = HISTORY_WINDOW,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    prior: dict[str, Any] | None = None
    history: list[dict[str, Any]] = []
    for end in range(MIN_CHART_CANDLES, len(candles) + 1):
        try:
            point = _replay_point(symbol, timeframe, candles[:end], prior, hysteresis_params)
        except ValueError:
            continue
        prior = point["state"]
        history.append(point)
    return (history[-history_limit:] if history_limit is not None else history), prior


def _replay_point(
    symbol: str,
    timeframe: str,
    candles: list[MarketCandle],
    prior: dict[str, Any] | None,
    hysteresis_params: dict[str, Any] | None,
) -> dict[str, Any]:
    current = candles[-1]
    snapshot = MarketSnapshot(
        symbol=symbol,
        timeframe=timeframe,
        price=current.close,
        change_24h=0.0,
        funding_rate=0.0,
        open_interest_change=0.0,
        candles=candles,
        provider="stance_replay",
    )
    prefix_analysis = build_chart_analysis(snapshot, None, None)
    generated_at = current.timestamp + timedelta(minutes=_timeframe_minutes(timeframe), seconds=1)
    confluence = build_confluence(
        symbol=symbol,
        timeframe=timeframe,
        analysis=prefix_analysis,
        generated_at=generated_at,
        prior_state=prior,
        hysteresis_params=hysteresis_params,
    )
    return _point(current.timestamp, confluence)


def _point(timestamp: datetime, confluence: dict[str, Any]) -> dict[str, Any]:
    state = confluence.get("stance_state") if isinstance(confluence.get("stance_state"), dict) else {}
    preview = state.get("preview") if isinstance(state.get("preview"), dict) else {}
    return {
        "time": int(timestamp.timestamp()),
        "stance": state.get("stance") or confluence.get("stance") or "insufficient",
        "preview_stance": preview.get("raw_stance"),
        "transitioning": bool(state.get("transitioning")),
        "flipped": bool(state.get("flipped")),
        "long_evidence_count": len(confluence.get("long_evidence") or []),
        "short_evidence_count": len(confluence.get("short_evidence") or []),
        "confidence": _confidence(state, confluence),
        "reason": _reason(confluence),
        "state": state,
    }


def _with_live_endpoint(
    history: list[dict[str, Any]],
    confluence: dict[str, Any],
    last_time: datetime,
) -> list[dict[str, Any]]:
    live = _point(last_time, confluence)
    if history and history[-1]["time"] == live["time"]:
        history[-1] = live
    else:
        history.append(live)
    return [{key: value for key, value in item.items() if key != "state"} for item in history[-HISTORY_WINDOW:]]


def _live_only(confluence: dict[str, Any], candles: list[MarketCandle]) -> list[dict[str, Any]]:
    if not candles:
        return []
    return _with_live_endpoint([], confluence, candles[-1].timestamp)


def _can_increment(cached: dict[str, Any], candles: list[MarketCandle]) -> bool:
    if len(candles) < 2 or cached.get("prior_state") is None:
        return False
    return cached.get("last_time") == candles[-2].timestamp


def _candles(analysis: dict[str, Any]) -> list[MarketCandle]:
    result: list[MarketCandle] = []
    for item in analysis.get("candles") if isinstance(analysis.get("candles"), list) else []:
        if not isinstance(item, dict):
            continue
        try:
            result.append(
                MarketCandle(
                    timestamp=datetime.fromtimestamp(int(item["time"]), tz=timezone.utc),
                    open=float(item["open"]),
                    high=float(item["high"]),
                    low=float(item["low"]),
                    close=float(item["close"]),
                    volume=float(item.get("volume") or 0.0),
                    session=item.get("session"),
                    is_regular_session=item.get("is_regular_session"),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(result, key=lambda candle: candle.timestamp)


def _confidence(state: dict[str, Any], confluence: dict[str, Any]) -> float:
    long_score = float(state.get("long_score_ema") or confluence.get("long_score") or 0.0)
    short_score = float(state.get("short_score_ema") or confluence.get("short_score") or 0.0)
    total = long_score + short_score
    return round(abs(long_score - short_score) / total, 3) if total > 0 else 0.0


def _live_state(confluence: dict[str, Any]) -> dict[str, Any] | None:
    value = confluence.get("stance_state")
    return dict(value) if isinstance(value, dict) else None


def _reason(confluence: dict[str, Any]) -> str:
    stance = str(confluence.get("stance") or "insufficient")
    key = "long_evidence" if stance == "long_leaning" else "short_evidence" if stance == "short_leaning" else None
    evidence = confluence.get(key) if key and isinstance(confluence.get(key), list) else []
    if evidence and isinstance(evidence[0], dict):
        return str(evidence[0].get("claim") or "스탠스 전환")
    return "상방·하방 근거 균형 변화"


def _timeframe_minutes(timeframe: str) -> int:
    value = TIMEFRAME_MINUTES.get(timeframe)
    return int(value if value is not None else 240)
