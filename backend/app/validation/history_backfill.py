"""WO-FCE-REPLAY-DEPTH-01 4-2 — 라이브 유니버스 캔들 영속화.

## 왜

재판정 기반이 없어서 같은 사고가 반복됐다:

- `RISK-SIZING-01` Phase 2 가 손절 체결 반사실을 **크립토 봉 미보존**으로 포기했다
- `POSITION-VIEW-01` 이 국면-추세 상충 발생률을 **원리적 산출 불가**로 남겼다
- Phase 1~4 의 반사실이 커밋되지 않은 임시 스크립트로 산출돼 **기준선을 재현할 수 없게 됐다**

## 깊은 로더는 이미 있었다. 대상 심볼이 없었을 뿐이다

```
provider.get_history_ohlcv_async(limit=2_196)   페이지네이션 · 최대 5,000 · 실측 1.6초/심볼
backtest/stance_validation.py:68                유일한 기록 경로. 대상은 DEFAULT_SYMBOLS
```

실측 2026-08-21: `stance_history_candles` 는 **3심볼 5,577행**(BTC 2391 · ETH 2391 · SOXL 795)
이고 라이브 유니버스 13종과의 **교집합이 SOXLUSDT 하나**였다. 저장 경로가 한 잡의 부산물이라
라이브가 실제로 평가하는 심볼과 어긋나 있었다.

## 이 모듈이 하는 일

라이브 유니버스 심볼을 **같은 경로로** 수집·저장한다(중복 구현 금지 — `get_history_ohlcv` +
`upsert_stance_history_candles` 재사용). 추가하는 것은 **대상 선정 · 증분 갱신 · 리텐션**뿐이다.

- **증분**: 매번 2,196봉을 다시 받지 않는다. 저장된 마지막 봉 이후만 요청한다
- **리텐션**(C7): 심볼당 보존 봉 수 상한. DB 12.8GB 선례가 있으므로 **함께** 배선한다
- **옵트인**(C5): 기본값은 수집하지 않음. 파일 한 값으로 원복

⚠️ **이 모듈은 분석 페이로드를 바꾸지 않는다.** 저장만 한다 — 휴면 게이트(`stage2_template`)를
깨우는 것은 4-3 소관이며 별도 옵트인이다(§2 위험).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, Sequence

from app.db.models import MarketCandle, utc_now


# 심볼당 보존 봉 수 상한 (C7). 4시간봉 2,196 = 약 366일이며 재판정에 충분하다.
# 상한을 두는 이유는 DB 12.8GB 비대 선례다 — "나중에 리텐션"은 오지 않는다.
DEFAULT_RETENTION_BARS = 2_196

# 한 번에 요청할 최대 봉 수. provider 상한(5,000)보다 낮게 둔다.
DEFAULT_HISTORY_BARS = 2_196

# 증분 갱신 시 겹쳐 받는 봉 수. 경계에서 봉이 새로 확정되며 값이 바뀔 수 있으므로
# 마지막 저장 봉 직후부터가 아니라 **약간 겹쳐** 받아 덮어쓴다.
INCREMENTAL_OVERLAP_BARS = 3


@dataclass(frozen=True)
class BackfillResult:
    symbol: str
    timeframe: str
    requested_bars: int
    fetched: int
    stored: int
    # 저장소에서 **실제로 지워진** 행 수. 보고와 실제가 갈라지면 리텐션은 없는 것보다 나쁘다.
    pruned: int
    mode: str  # full | incremental | skipped
    reason: str | None = None
    # 쓰기 대상에서 잘라낸 봉 수(이미 저장돼 있지 않던 오래된 봉). `pruned` 와 다른 축이다 —
    # 하나는 "안 썼다", 하나는 "지웠다"이고 둘을 합치면 어느 쪽도 검증할 수 없다.
    trimmed: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "requested_bars": self.requested_bars,
            "fetched": self.fetched,
            "stored": self.stored,
            "pruned": self.pruned,
            "trimmed": self.trimmed,
            "mode": self.mode,
            "reason": self.reason,
        }


def timeframe_seconds(timeframe: str) -> int:
    unit = timeframe[-1:].lower()
    try:
        amount = int(timeframe[:-1])
    except ValueError:
        return 14_400
    return amount * {"m": 60, "h": 3_600, "d": 86_400, "w": 604_800}.get(unit, 3_600)


def plan_request_bars(
    *,
    stored_latest: datetime | None,
    now: datetime,
    timeframe: str,
    history_bars: int = DEFAULT_HISTORY_BARS,
) -> tuple[int, str]:
    """이번에 몇 봉을 요청할지와 그 이유.

    저장분이 없으면 전량(`full`). 있으면 **경과한 봉 수 + 겹침**만 요청한다(`incremental`).
    이미 최신이면 겹침 분량만 받아 마지막 봉의 확정값을 갱신한다.
    """
    if stored_latest is None:
        return history_bars, "full"
    elapsed = (now - stored_latest).total_seconds()
    bars = int(elapsed // max(1, timeframe_seconds(timeframe)))
    return max(INCREMENTAL_OVERLAP_BARS, min(history_bars, bars + INCREMENTAL_OVERLAP_BARS)), "incremental"


def apply_retention(candles: Sequence[MarketCandle], *, retention_bars: int = DEFAULT_RETENTION_BARS) -> tuple[list[MarketCandle], int]:
    """보존 상한을 넘는 **오래된** 봉을 떨어낸다. 최근 것을 남긴다."""
    ordered = sorted(candles, key=lambda candle: candle.timestamp)
    if retention_bars <= 0 or len(ordered) <= retention_bars:
        return ordered, 0
    return ordered[-retention_bars:], len(ordered) - retention_bars


def backfill_symbol(
    repo: Any,
    *,
    symbol: str,
    timeframe: str,
    history_loader: Callable[..., Iterable[Any]],
    now: datetime | None = None,
    history_bars: int = DEFAULT_HISTORY_BARS,
    retention_bars: int = DEFAULT_RETENTION_BARS,
) -> BackfillResult:
    """한 심볼의 히스토리를 증분 수집해 저장한다.

    실패는 **사유와 함께** 결과로 돌려준다 — 예외로 잡을 죽이면 나머지 심볼이 굶는다.
    """
    now = now or utc_now()
    normalized = symbol.upper()
    try:
        stored = repo.list_stance_history_candles(normalized, timeframe, limit=5_000)
    except Exception as exc:  # 저장분 조회 실패가 수집을 막지 않는다
        stored = []
        _ = exc
    stored_latest = max((candle.timestamp for candle in stored), default=None)
    requested, mode = plan_request_bars(stored_latest=stored_latest, now=now, timeframe=timeframe, history_bars=history_bars)

    try:
        raw = list(history_loader(normalized, timeframe, requested, now=now))
    except TypeError:
        # `now` 키워드를 받지 않는 로더도 있다.
        try:
            raw = list(history_loader(normalized, timeframe, requested))
        except Exception as exc:
            return BackfillResult(normalized, timeframe, requested, 0, 0, 0, "skipped", f"{type(exc).__name__}: {exc}")
    except Exception as exc:
        return BackfillResult(normalized, timeframe, requested, 0, 0, 0, "skipped", f"{type(exc).__name__}: {exc}")

    fetched = [_market_candle(candle) for candle in raw]
    fetched = [candle for candle in fetched if candle is not None]
    if not fetched:
        return BackfillResult(normalized, timeframe, requested, 0, 0, 0, mode, "empty_history")

    merged = {candle.timestamp: candle for candle in stored}
    merged.update({candle.timestamp: candle for candle in fetched})
    kept, _trimmed = apply_retention(list(merged.values()), retention_bars=retention_bars)

    written = repo.upsert_stance_history_candles(normalized, timeframe, kept, "replay_depth_backfill", now)
    # upsert 는 INSERT/UPDATE 만 한다 — 목록을 잘라내는 것만으로는 **이미 저장된 오래된 행이
    # 지워지지 않는다.** 실측: 리텐션 10 을 건 2회차 실행이 `pruned=45` 를 보고하면서 표는
    # 50행 → 55행으로 늘었다. 보고와 실제가 반대 방향인 리텐션은 없는 것보다 나쁘다.
    #
    # 그래서 삭제는 저장소에 시킨다. `pruned` 는 **실제로 지워진 행 수**다.
    pruned = _prune(repo, normalized, timeframe, retention_bars)
    return BackfillResult(normalized, timeframe, requested, len(fetched), int(written or len(kept)), pruned, mode, trimmed=_trimmed)


def _prune(repo: Any, symbol: str, timeframe: str, retention_bars: int) -> int:
    """저장소에 실제 삭제를 시킨다. 삭제를 지원하지 않는 저장소면 0 을 보고한다 —
    **지워졌다고 적지 않는다.**"""
    if retention_bars <= 0:
        return 0
    pruner = getattr(repo, "prune_stance_history_candles", None)
    if not callable(pruner):
        return 0
    try:
        return int(pruner(symbol, timeframe, retention_bars) or 0)
    except Exception:  # 리텐션 실패가 수집 결과를 버리게 하지 않는다
        return 0


def backfill_universe(
    repo: Any,
    *,
    pairs: Sequence[tuple[str, str]],
    history_loader: Callable[..., Iterable[Any]],
    now: datetime | None = None,
    history_bars: int = DEFAULT_HISTORY_BARS,
    retention_bars: int = DEFAULT_RETENTION_BARS,
    max_symbols: int | None = None,
) -> dict[str, Any]:
    """라이브 유니버스 전체를 수집한다.

    `max_symbols` 로 한 번에 처리할 심볼 수를 제한한다 — 실측 1.6초/심볼이지만 유니버스가
    커지면 잡 실행 시간이 길어지고, `WORKER-HANG-02` 가 고친 큐를 다시 막을 수 있다(C8).
    """
    now = now or utc_now()
    ordered = sorted({(symbol.upper(), timeframe) for symbol, timeframe in pairs})
    if max_symbols is not None:
        ordered = ordered[:max_symbols]
    results = [
        backfill_symbol(
            repo,
            symbol=symbol,
            timeframe=timeframe,
            history_loader=history_loader,
            now=now,
            history_bars=history_bars,
            retention_bars=retention_bars,
        )
        for symbol, timeframe in ordered
    ]
    stored = sum(item.stored for item in results)
    failed = [item.as_dict() for item in results if item.mode == "skipped" or item.reason]
    return {
        "symbols": len(results),
        "stored": stored,
        "pruned": sum(item.pruned for item in results),
        "trimmed": sum(item.trimmed for item in results),
        "incremental": sum(1 for item in results if item.mode == "incremental"),
        "full": sum(1 for item in results if item.mode == "full"),
        # C8 침묵 금지 — 실패한 심볼은 사유와 함께 남는다.
        "failures": failed,
        "results": [item.as_dict() for item in results],
        # 조기 반환(수집 대상 0)을 성공으로 세지 않는다.
        "effective_run": bool(results),
    }


def _market_candle(candle: Any) -> MarketCandle | None:
    timestamp = getattr(candle, "timestamp", None)
    if timestamp is None:
        return None
    try:
        return MarketCandle(
            timestamp=timestamp,
            open=float(candle.open),
            high=float(candle.high),
            low=float(candle.low),
            close=float(candle.close),
            volume=float(getattr(candle, "volume", 0.0) or 0.0),
        )
    except (AttributeError, TypeError, ValueError):
        return None


def coverage_report(repo: Any, *, pairs: Sequence[tuple[str, str]]) -> dict[str, Any]:
    """저장 실태와 라이브 유니버스의 교집합 (4-1 작업 3 · 4-2 검증).

    재판정 가능 범위를 이 값으로 판단한다 — 봉이 없으면 재판정이 불가능하다.
    """
    rows: list[dict[str, Any]] = []
    covered = 0
    for symbol, timeframe in sorted({(s.upper(), t) for s, t in pairs}):
        try:
            stored = repo.list_stance_history_candles(symbol, timeframe, limit=5_000)
        except Exception:
            stored = []
        count = len(stored)
        if count >= 200:
            covered += 1
        rows.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "bars": count,
                "from": min((c.timestamp for c in stored), default=None),
                "to": max((c.timestamp for c in stored), default=None),
                "replayable": count >= 200,
            }
        )
    return {
        "universe_size": len(rows),
        "replayable_symbols": covered,
        "total_bars": sum(row["bars"] for row in rows),
        "rows": rows,
    }
