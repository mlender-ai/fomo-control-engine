"""WO-FCE-STOCK-UNBLOCK-01 — `stage2_template` 조건별 계측 회귀.

이 파일이 지키는 것:
1. **`active` 판정이 바뀌지 않는다** (C4) — 관측 필드만 추가됐다.
2. **평가 불가와 실패가 구분된다** — `None` vs `False`. 이 구분이 없어 두 달 오해됐다.
3. **임계값이 그대로다** (C1) — 200 · 정배열 · 기울기 · −25%.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db.models import MarketCandle
from app.structure.candidates.engine import detect_stage2_template


BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _candles(count: int, *, start: float = 100.0, step: float = 0.1, tail_drop_pct: float = 0.0) -> list[MarketCandle]:
    rows: list[MarketCandle] = []
    for index in range(count):
        close = start + index * step
        rows.append(
            MarketCandle(
                timestamp=BASE + timedelta(days=index),
                open=close,
                high=close + 1.0,
                low=close - 1.0,
                close=close,
                volume=1_000.0,
            )
        )
    if tail_drop_pct and rows:
        peak = rows[-1].close
        dropped = peak * (1.0 - tail_drop_pct / 100.0)
        rows[-1] = rows[-1].model_copy(update={"close": dropped, "low": dropped - 1.0})
    return rows


# ---------------------------------------------------------------------------
# 조건 1 — 캔들 부족은 "평가 불가"다
# ---------------------------------------------------------------------------


def test_short_series_marks_other_conditions_unevaluated() -> None:
    """평가 불가를 `False` 로 적으면 '조건이 틀렸다'로 읽힌다 — 그게 이번 오해의 원인이었다."""
    result = detect_stage2_template(_candles(140))
    assert result["active"] is False
    assert result["candle_count"] == 140
    checks = result["checks"]
    assert checks["candle_count_ok"] is False
    assert checks["alignment_ok"] is None
    assert checks["slope_ok"] is None
    assert checks["high_distance_ok"] is None


def test_the_pipeline_ceiling_of_199_is_still_short() -> None:
    """실측 파이프라인 공급값 199 — 게이트 요구 200 에 **1개** 못 미친다."""
    result = detect_stage2_template(_candles(199))
    assert result["candle_count"] == 199
    assert result["checks"]["candle_count_ok"] is False
    assert result["checks"]["alignment_ok"] is None


def test_two_hundred_candles_finally_evaluate_the_real_conditions() -> None:
    """200개가 오면 조건 2·3·4 가 처음으로 실행된다."""
    result = detect_stage2_template(_candles(200))
    checks = result["checks"]
    assert checks["candle_count_ok"] is True
    assert checks["alignment_ok"] is not None
    assert checks["slope_ok"] is not None
    assert checks["high_distance_ok"] is not None


def test_empty_series_is_handled() -> None:
    result = detect_stage2_template([])
    assert result["active"] is False
    assert result["candle_count"] == 0
    assert result["checks"]["candle_count_ok"] is False


# ---------------------------------------------------------------------------
# C4 — `active` 판정 로직은 그대로다
# ---------------------------------------------------------------------------


def test_rising_series_is_active_and_all_checks_pass() -> None:
    result = detect_stage2_template(_candles(260))
    assert result["active"] is True
    assert all(result["checks"].values())


def test_active_equals_the_conjunction_of_its_checks() -> None:
    """`active` 가 조건들의 논리곱과 일치한다 — 계측이 판정과 어긋나면 둘 중 하나는 거짓말이다."""
    for count, drop in ((260, 0.0), (260, 40.0), (200, 0.0), (240, 10.0)):
        result = detect_stage2_template(_candles(count, tail_drop_pct=drop))
        checks = result["checks"]
        if checks["candle_count_ok"]:
            expected = bool(checks["alignment_ok"] and checks["slope_ok"] and checks["high_distance_ok"])
            assert result["active"] is expected, (count, drop, checks)


def test_deep_drawdown_fails_only_the_distance_condition() -> None:
    """−25% 조건만 떨어지는 경우 — 어느 조건이 걸렀는지 구분된다 (C8 침묵 금지)."""
    result = detect_stage2_template(_candles(260, tail_drop_pct=40.0))
    assert result["active"] is False
    assert result["checks"]["candle_count_ok"] is True
    assert result["checks"]["high_distance_ok"] is False


def test_flat_series_fails_slope() -> None:
    result = detect_stage2_template(_candles(260, step=0.0))
    assert result["checks"]["slope_ok"] is False


# ---------------------------------------------------------------------------
# C1 — 임계값 무변경
# ---------------------------------------------------------------------------


def test_thresholds_are_unchanged() -> None:
    """200 · −25% 가 그대로인지 경계에서 확인한다."""
    assert detect_stage2_template(_candles(199))["checks"]["candle_count_ok"] is False
    assert detect_stage2_template(_candles(200))["checks"]["candle_count_ok"] is True
    # 고점 대비 −25% 경계: 24% 하락은 통과, 26% 하락은 탈락
    assert detect_stage2_template(_candles(260, tail_drop_pct=24.0))["checks"]["high_distance_ok"] is True
    assert detect_stage2_template(_candles(260, tail_drop_pct=26.0))["checks"]["high_distance_ok"] is False


def test_observation_fields_do_not_replace_existing_payload() -> None:
    """기존 필드가 사라지면 화면·집계가 조용히 깨진다."""
    result = detect_stage2_template(_candles(260))
    for key in ("active", "label", "ma150", "ma200", "ma200_slope_20", "high_distance_pct", "as_of"):
        assert key in result
