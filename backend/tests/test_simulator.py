from app.positions.simulator import estimate_liquidation, simulate_entry


def _analysis(
    mark: float,
    support=None,
    resistance=None,
    invalidation=None,
    funding=0.0,
    volume_state="balanced_flow",
    alignment="neutral",
):
    return {
        "mark_price": mark,
        "funding_rate": funding,
        "price_levels": {
            "entry": mark,
            "mark": mark,
            "liquidation": None,
            "support": support or [],
            "resistance": resistance or [],
            "invalidation": invalidation or [],
        },
        "volume_profile": {
            "poc_price": mark,
            "value_area_high": mark * 1.05,
            "value_area_low": mark * 0.95,
        },
        "volume_xray": {"volume_state": volume_state, "spike_detected": False},
        "harmonic_patterns": [],
        "candles": [],
        "wyckoff_mtf": {"htf_phase": None, "htf_trend": None, "alignment": alignment},
    }


def test_liquidation_long_below_entry():
    liq = estimate_liquidation(100.0, 10, "long")
    assert liq is not None and liq < 100.0
    # 10x long ≈ entry × (1 - 0.1 + 0.005 + 0.0006) ≈ 90.56
    assert 90.0 < liq < 91.0


def test_liquidation_short_above_entry():
    liq = estimate_liquidation(100.0, 10, "short")
    assert liq is not None and liq > 100.0
    assert 109.0 < liq < 110.0


def test_liquidation_higher_leverage_closer():
    near = estimate_liquidation(100.0, 20, "long")
    far = estimate_liquidation(100.0, 5, "long")
    assert near > far  # 20x 청산가가 진입가에 더 가깝다(높다)


def test_survival_warning_when_stop_beyond_liquidation():
    # 손절을 청산보다 먼 -12%에 두면(10x long 청산 ≈ -9.4%) 생존 마진 실패
    analysis = _analysis(
        100.0,
        support=[{"price": 88.0, "score": 70, "label": "지지"}],
        resistance=[{"price": 110.0, "score": 70, "label": "저항"}],
    )
    result = simulate_entry(
        symbol="BTCUSDT",
        direction="long",
        entry_price=100.0,
        leverage=10,
        margin_usdt=100,
        margin_mode="isolated",
        chart_analysis=analysis,
        mmr=None,
        direction_score=60,
    )
    assert result["survives_to_invalidation"] is False
    survival = next(item for item in result["checklist"] if item["key"] == "survival")
    assert survival["status"] == "fail"
    assert "청산" in survival["reason"]


def test_survival_pass_when_stop_inside_liquidation():
    # 손절 -4%가 청산 -9.4%보다 안쪽 → 생존 통과
    analysis = _analysis(
        100.0,
        support=[{"price": 96.0, "score": 70, "label": "지지"}],
        resistance=[{"price": 112.0, "score": 70, "label": "저항"}],
    )
    result = simulate_entry(
        symbol="BTCUSDT",
        direction="long",
        entry_price=100.0,
        leverage=10,
        margin_usdt=100,
        margin_mode="isolated",
        chart_analysis=analysis,
        mmr=None,
        direction_score=60,
    )
    assert result["survives_to_invalidation"] is True


def test_rr_ratio_and_usdt_amounts():
    # 손절 -4% (지지 96), 익절 +8% (저항 108) → R:R 2.0
    analysis = _analysis(
        100.0,
        support=[{"price": 96.0, "score": 70, "label": "지지"}],
        resistance=[{"price": 108.0, "score": 70, "label": "저항"}],
    )
    result = simulate_entry(
        symbol="BTCUSDT",
        direction="long",
        entry_price=100.0,
        leverage=10,
        margin_usdt=100,
        margin_mode="isolated",
        chart_analysis=analysis,
        mmr=None,
        direction_score=60,
    )
    assert result["rr_ratio"] == 2.0
    # 손실 = 100 × 10 × 4% = 40, 이익 = 100 × 10 × 8% = 80
    assert result["loss_usdt"] == 40.0
    assert result["profit_usdt"] == 80.0
    rr_item = next(item for item in result["checklist"] if item["key"] == "rr")
    assert rr_item["status"] == "pass"


def test_rr_fail_below_threshold():
    # 손절 -8% (지지 92), 익절 +4% (저항 104) → R:R 0.5 < 1.5
    analysis = _analysis(
        100.0,
        support=[{"price": 92.0, "score": 70, "label": "지지"}],
        resistance=[{"price": 104.0, "score": 70, "label": "저항"}],
    )
    result = simulate_entry(
        symbol="BTCUSDT",
        direction="long",
        entry_price=100.0,
        leverage=5,
        margin_usdt=100,
        margin_mode="isolated",
        chart_analysis=analysis,
        mmr=None,
        direction_score=60,
    )
    assert result["rr_ratio"] == 0.5
    rr_item = next(item for item in result["checklist"] if item["key"] == "rr")
    assert rr_item["status"] == "fail"


def test_htf_conflict_checklist_fail():
    analysis = _analysis(
        100.0,
        support=[{"price": 96.0, "score": 70, "label": "지지"}],
        resistance=[{"price": 108.0, "score": 70, "label": "저항"}],
        alignment="conflicting",
    )
    result = simulate_entry(
        symbol="BTCUSDT",
        direction="long",
        entry_price=100.0,
        leverage=10,
        margin_usdt=100,
        margin_mode="isolated",
        chart_analysis=analysis,
        mmr=None,
        direction_score=60,
    )
    assert result["htf_conflict"] is True
    htf = next(item for item in result["checklist"] if item["key"] == "htf")
    assert htf["status"] == "fail"


def test_funding_adverse_for_long():
    analysis = _analysis(
        100.0,
        support=[{"price": 96.0, "score": 70, "label": "지지"}],
        resistance=[{"price": 108.0, "score": 70, "label": "저항"}],
        funding=0.001,
    )
    result = simulate_entry(
        symbol="BTCUSDT",
        direction="long",
        entry_price=100.0,
        leverage=10,
        margin_usdt=100,
        margin_mode="isolated",
        chart_analysis=analysis,
        mmr=None,
        direction_score=60,
    )
    funding = next(item for item in result["checklist"] if item["key"] == "funding")
    assert funding["status"] == "fail"


def test_drying_up_volume_fails():
    analysis = _analysis(
        100.0,
        support=[{"price": 96.0, "score": 70, "label": "지지"}],
        resistance=[{"price": 108.0, "score": 70, "label": "저항"}],
        volume_state="drying_up",
    )
    result = simulate_entry(
        symbol="BTCUSDT",
        direction="long",
        entry_price=100.0,
        leverage=10,
        margin_usdt=100,
        margin_mode="isolated",
        chart_analysis=analysis,
        mmr=None,
        direction_score=60,
    )
    vol = next(item for item in result["checklist"] if item["key"] == "volume")
    assert vol["status"] == "fail"


def test_checklist_counts_exclude_na():
    analysis = _analysis(100.0)  # no levels → several NA
    result = simulate_entry(
        symbol="BTCUSDT",
        direction="long",
        entry_price=100.0,
        leverage=10,
        margin_usdt=100,
        margin_mode="isolated",
        chart_analysis=analysis,
        mmr=None,
        direction_score=60,
    )
    assert result["checklist_total"] == sum(1 for item in result["checklist"] if item["status"] != "na")
    assert result["checklist_passed"] <= result["checklist_total"]
