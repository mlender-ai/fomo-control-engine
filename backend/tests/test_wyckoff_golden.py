"""WO-FCE-43 Part B — 와이코프 골든 케이스 감사.

교과서 매집/분산 시퀀스 합성 픽스처 6종 + NBISUSDT류 모순 출력(매집+UTAD) 회귀.
케이스별 기대/실제 대조는 docs/WyckoffAudit.md 참조.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db.models import MarketCandle
from app.structure.wyckoff.engine import analyze_wyckoff

BASE = datetime(2025, 1, 1, tzinfo=timezone.utc)
SUPPORT = 100.0
RESISTANCE = 110.0

LEVELS = {
    "support": [{"price": SUPPORT, "score": 80, "touches": 5, "kind": "support", "sources": ["swing"]}],
    "resistance": [{"price": RESISTANCE, "score": 80, "touches": 5, "kind": "resistance", "sources": ["swing"]}],
}


def _candle(index: int, *, open_: float, high: float, low: float, close: float, volume: float = 100.0) -> MarketCandle:
    return MarketCandle(
        timestamp=BASE + timedelta(hours=4 * index),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _range_base(n: int = 60) -> list[MarketCandle]:
    """지지 100 / 저항 110 박스 안에서 진동하는 베이스 캔들."""
    candles = []
    for i in range(n):
        mid = 104.0 + (i % 5) * 0.6  # 104.0~106.4 진동
        candles.append(_candle(i, open_=mid - 0.3, high=mid + 1.0, low=mid - 1.0, close=mid + 0.3))
    return candles


def _put(candles: list[MarketCandle], index: int, **kwargs) -> None:
    candles[index] = _candle(index, **kwargs)


def _accumulation_full() -> list[MarketCandle]:
    """골든 #1: SC → AR → ST → Spring → SOS (→ LPS 파생) 완전 매집 시퀀스."""
    candles = _range_base()
    _put(candles, 10, open_=103.5, high=104.2, low=100.3, close=103.0, volume=260)  # SC
    _put(candles, 12, open_=101.5, high=106.5, low=100.8, close=106.0, volume=140)  # AR
    _put(candles, 20, open_=102.0, high=102.8, low=100.5, close=102.0, volume=85)  # ST
    _put(candles, 35, open_=100.8, high=101.8, low=99.3, close=101.5, volume=95)  # Spring
    _put(candles, 50, open_=109.5, high=111.4, low=109.4, close=110.9, volume=150)  # SOS
    _put(candles, 52, open_=110.6, high=110.9, low=109.6, close=110.4, volume=95)  # LPS 되돌림
    for i in range(53, 60):
        _put(candles, i, open_=110.4, high=111.4, low=110.1, close=110.8, volume=100)
    return candles


def _distribution_full() -> list[MarketCandle]:
    """골든 #2: BC → AR(반락) → ST → UTAD → SOW (→ LPSY 파생) 분산 대칭."""
    candles = _range_base()
    _put(candles, 10, open_=106.5, high=109.7, low=105.8, close=106.5, volume=260)  # BC
    _put(candles, 12, open_=108.5, high=109.2, low=103.5, close=104.0, volume=140)  # AR(반락)
    _put(candles, 20, open_=108.0, high=109.5, low=107.2, close=108.0, volume=85)  # ST(저항)
    _put(candles, 35, open_=109.2, high=110.7, low=108.4, close=108.6, volume=95)  # UTAD
    _put(candles, 50, open_=100.5, high=100.6, low=98.9, close=99.2, volume=150)  # SOW
    _put(candles, 52, open_=99.4, high=100.3, low=98.9, close=99.6, volume=95)  # LPSY 되돌림
    for i in range(53, 60):
        _put(candles, i, open_=99.5, high=99.9, low=98.6, close=99.2, volume=100)
    return candles


def _trend(direction: str, n: int = 80) -> list[MarketCandle]:
    """골든 #3/#4: 명확한 박스 없는 단방향 추세."""
    candles = []
    price = 100.0
    step = 1.2 if direction == "up" else -1.2
    for i in range(n):
        price += step
        candles.append(_candle(i, open_=price - step * 0.8, high=price + 0.8, low=price - 0.8, close=price))
    return candles


def _mixed() -> list[MarketCandle]:
    """골든 #5: Spring과 UTAD가 비슷한 신뢰도로 공존 — 억지 판정 금지."""
    candles = _range_base()
    _put(candles, 30, open_=100.8, high=101.6, low=99.4, close=101.2, volume=100)  # Spring
    _put(candles, 40, open_=109.3, high=110.6, low=108.6, close=108.9, volume=100)  # UTAD
    return candles


def _accumulation_phase_a() -> list[MarketCandle]:
    """골든 #6: SC/AR/ST만 — Phase A 초기 매집."""
    candles = _range_base()
    _put(candles, 10, open_=103.5, high=104.2, low=100.3, close=103.0, volume=260)  # SC
    _put(candles, 12, open_=101.5, high=106.5, low=100.8, close=106.0, volume=140)  # AR
    _put(candles, 20, open_=102.0, high=102.8, low=100.5, close=102.0, volume=85)  # ST
    return candles


def _accumulation_with_upthrust() -> list[MarketCandle]:
    """모순 회귀: 매집 우세 레인지 + 저항 상단 스윕 (NBISUSDT류 '매집 Phase A + UTAD')."""
    candles = _range_base()
    _put(candles, 10, open_=103.5, high=104.2, low=100.3, close=103.0, volume=260)  # SC
    _put(candles, 20, open_=102.0, high=102.8, low=100.5, close=102.0, volume=85)  # ST
    _put(candles, 35, open_=100.5, high=101.8, low=98.4, close=101.5, volume=130)  # 깊은 Spring (강)
    _put(candles, 45, open_=109.4, high=110.6, low=108.9, close=109.2, volume=130)  # 상단 스윕 (중)
    return candles


# ── 골든 케이스 ────────────────────────────────────────────────────

def test_golden_1_accumulation_full_sequence() -> None:
    result = analyze_wyckoff(_accumulation_full(), levels=LEVELS, include_mtf=False)
    assert result["side"] == "accumulation"
    assert result["phase"] in {"accumulation_phase_d", "accumulation_phase_e"}
    assert result["spring_candidate"] is True
    assert result["sos_confirmed"] is True
    types = {event["type"] for event in result["events"]}
    assert {"selling_climax", "spring_candidate", "sos_confirmed"} <= types


def test_golden_2_distribution_full_sequence() -> None:
    result = analyze_wyckoff(_distribution_full(), levels=LEVELS, include_mtf=False)
    assert result["side"] == "distribution"
    assert result["phase"] in {"distribution_phase_d", "distribution_phase_e"}
    assert result["utad_candidate"] is True
    assert result["sow_confirmed"] is True
    # 분산 판정에서는 UTAD 라벨 유지 (재명명은 매집 맥락 한정).
    utad = next(event for event in result["events"] if event["type"] == "utad_candidate")
    assert utad["label"] == "UTAD"


def test_golden_3_uptrend_no_range() -> None:
    result = analyze_wyckoff(_trend("up"), levels=None, include_mtf=False)
    assert result["phase"] == "trending"
    assert result["events"] == []
    assert result["side"] == "neutral"


def test_golden_4_downtrend_no_range() -> None:
    result = analyze_wyckoff(_trend("down"), levels=None, include_mtf=False)
    assert result["phase"] == "trending"
    assert result["events"] == []


def test_golden_5_mixed_signals_stay_undetermined() -> None:
    result = analyze_wyckoff(_mixed(), levels=LEVELS, include_mtf=False)
    assert result["phase"] == "undetermined"
    assert result["side"] == "neutral"


def test_golden_6_accumulation_phase_a_only() -> None:
    result = analyze_wyckoff(_accumulation_phase_a(), levels=LEVELS, include_mtf=False)
    assert result["side"] == "accumulation"
    assert result["phase"] == "accumulation_phase_a"
    assert result["spring_candidate"] is False
    assert result["sos_confirmed"] is False


# ── 모순 출력 회귀 (매집 + UTAD 동시) ──────────────────────────────

def test_contradiction_upthrust_relabeled_in_accumulation() -> None:
    result = analyze_wyckoff(_accumulation_with_upthrust(), levels=LEVELS, include_mtf=False)
    assert result["side"] == "accumulation"
    upthrusts = [event for event in result["events"] if event["type"] == "utad_candidate"]
    assert upthrusts, "상단 스윕 이벤트가 감지되어야 함"
    for event in upthrusts:
        # 매집 맥락에서는 UTAD(분산 전제 명칭)가 아니라 UT로 재명명 — 자기모순 제거.
        assert event["label"] == "UT"
        assert "UTAD 아님" in event["context_note"]
    # 반대측 고신뢰 이벤트 공존은 은폐하지 않고 conflict_note로 명시.
    if max(event["confidence"] for event in upthrusts) >= 65:
        assert result["conflict_note"] is not None
        assert "혼합 신호" in result["conflict_note"]


def test_undetermined_range_reports_hold_not_forced_phase() -> None:
    # 이벤트 근거가 부족한 레인지 — 억지 phase 금지 재검증.
    result = analyze_wyckoff(_range_base(), levels=LEVELS, include_mtf=False)
    assert result["side"] in {"neutral"}
    assert result["phase"] in {"undetermined", "trending"}
