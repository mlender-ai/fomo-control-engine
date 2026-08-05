"""WO-FCE-OBSERVATION-INTEGRITY-01 Phase 2-2/2-4 — 캔들 커버리지·리샘플 정합.

실측이 WO 가설을 반증했다. "1분봉 count=200(3.3시간)이라 정규장 6.5시간을 못 채운다"가 아니었다.
15초마다 200개를 다시 받으므로 합집합은 전 구간을 덮는다. 낮았던 커버리지는 KST 롤오버 결함으로
수집 자체가 15:00 UTC 에 끊긴 결과였다:

    TSLA 1분봉 정규장 커버리지: 08-03 23.6% · 08-04 23.1% · **08-05 100%** (수리 `103fd09` 당일)
    LRCX                        08-03 17.4% · 08-04 23.1% · **08-05 100%**

그래서 `count` 를 올리지 않는다(불필요한 TPS 부담). 대신 같은 정지가 재발하면
**차트가 조용히 이어 붙는 대신 갭으로 드러나게** 한다.

고정하는 명제:
1. 시간축 공백은 감지되고 커버리지 %로 노출된다 (C3 — 공백을 이어 붙이지 않는다)
2. 리샘플은 결손 구간에서 온전한 봉인 척하지 않는다
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.stock_paper.service import candle_coverage
from app.toss.signals import resample_candles

START = datetime(2026, 8, 5, 13, 30, tzinfo=timezone.utc)


def _bars(count: int, *, skip: set[int] | None = None, step_minutes: int = 1) -> list[dict]:
    skip = skip or set()
    rows = []
    for index in range(count):
        if index in skip:
            continue
        stamp = START + timedelta(minutes=index * step_minutes)
        rows.append({"opened_at": stamp.isoformat(), "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10.0})
    return rows


# ── 1. 커버리지·공백 감지 ───────────────────────────────────────────


def test_continuous_session_is_full_coverage() -> None:
    """08-05 실측 재현: 정규장 390분 연속 → 100%, 공백 0."""
    coverage = candle_coverage(_bars(390), "1m")
    assert coverage["pct"] == 100.0
    assert coverage["gap_count"] == 0
    assert coverage["observed"] == 390


def test_mid_session_gap_is_detected_not_smoothed() -> None:
    """공백을 조용히 이어 붙이면 거짓 차트다 — 반드시 갭으로 드러나야 한다."""
    coverage = candle_coverage(_bars(60, skip=set(range(20, 35))), "1m")

    assert coverage["gap_count"] == 1
    assert coverage["missing_bars"] == 15
    assert coverage["pct"] < 100.0
    gap = coverage["gaps"][0]
    assert gap["missing_bars"] == 15
    assert gap["backfillable"] is True


def test_rollover_style_truncation_shows_as_low_coverage() -> None:
    """수리 전 실측 재현: 정규장 초반 90분만 있고 나머지가 없는 형태."""
    early = _bars(90)
    late = [{**row, "opened_at": (START + timedelta(minutes=389)).isoformat()} for row in _bars(1)]
    coverage = candle_coverage(early + late, "1m")

    assert coverage["pct"] < 30, f"{coverage['pct']}% — 조각난 차트가 정상으로 보이면 안 된다"
    assert coverage["gap_count"] == 1
    assert coverage["gaps"][0]["backfillable"] is False, "200봉 창을 넘는 공백은 재수집으로 못 메운다"


def test_short_series_does_not_crash() -> None:
    assert candle_coverage([], "1m")["pct"] == 0.0
    assert candle_coverage(_bars(1), "1m")["pct"] == 100.0


def test_daily_timeframe_uses_its_own_step() -> None:
    """1d 봉을 1분 간격으로 재면 커버리지가 0에 수렴한다 — timeframe 별 step 을 써야 한다."""
    coverage = candle_coverage(_bars(5, step_minutes=1440), "1d")
    assert coverage["pct"] == 100.0
    assert coverage["gap_count"] == 0


# ── 2. 리샘플 정합 (2-4) ────────────────────────────────────────────


def test_resample_marks_incomplete_buckets() -> None:
    """5분 중 1분만 있는 버킷을 온전한 5분봉처럼 내놓으면 거짓이다."""
    rows = _bars(5, skip={1, 2, 3, 4})  # 첫 버킷에 1봉만
    bars = resample_candles(rows, 5)

    assert len(bars) == 1
    assert bars[0]["source_bars"] == 1
    assert bars[0]["complete"] is False


def test_resample_marks_complete_buckets() -> None:
    bars = resample_candles(_bars(10), 5)
    assert len(bars) == 2
    assert all(bar["complete"] for bar in bars)
    assert all(bar["source_bars"] == 5 for bar in bars)


def test_resample_ohlc_stays_correct_across_a_gap() -> None:
    """결손이 있어도 남은 봉의 OHLC 는 정확해야 한다 — 값을 지어내지 않는다."""
    rows = _bars(5, skip={1, 3})
    rows[0]["high"] = 120.0
    rows[-1]["low"] = 80.0

    bar = resample_candles(rows, 5)[0]

    assert bar["high"] == 120.0
    assert bar["low"] == 80.0
    assert bar["source_bars"] == 3
    assert bar["complete"] is False
