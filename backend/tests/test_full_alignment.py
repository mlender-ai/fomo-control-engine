from app.analyst.alignment import build_full_alignment


def _confluence(*, dissent: bool = False, transitioning: bool = False, htf_bias: str = "long") -> dict:
    engines = ["liquidity", "wyckoff", "harmonic", "level"]
    return {
        "long_evidence": [
            {"engine": engine, "direction": "long", "score": 10 + index, "claim": f"{engine} long"}
            for index, engine in enumerate(engines)
        ],
        "short_evidence": ([{"engine": "volume", "direction": "short", "score": 9, "claim": "volume short"}] if dissent else []),
        "htf_context": {"bias": htf_bias, "alignment": "aligned" if htf_bias == "long" else "conflicting"},
        "stance_state": {"transitioning": transitioning, "candles_in_state": 3, "last_bar_at": "2026-07-12T00:00:00+00:00"},
    }


def _history(*, include_dissent: bool = False) -> dict:
    engines = ["liquidity", "wyckoff", "harmonic", "level"]
    stats = [
        {"lifecycle_state": "validated", "signature": {"engine": engine, "direction": "long"}}
        for engine in engines
    ]
    if include_dissent:
        stats.append({"lifecycle_state": "validated", "signature": {"engine": "volume", "direction": "short"}})
    return {"stats": stats, "event_stats": []}


def test_full_alignment_requires_four_validated_votes_and_htf_alignment() -> None:
    result = build_full_alignment(_confluence(), _history())

    assert result["unanimous"] is True
    assert result["direction"] == "long"
    assert result["agreeing"] == 4
    assert result["dissenting"] == 0
    assert result["score"] == 46.0


def test_full_alignment_rejects_one_dissent_transition_and_htf_conflict() -> None:
    assert build_full_alignment(_confluence(dissent=True), _history(include_dissent=True))["unanimous"] is False
    assert build_full_alignment(_confluence(transitioning=True), _history())["unanimous"] is False
    assert build_full_alignment(_confluence(htf_bias="short"), _history())["unanimous"] is False


def test_non_validated_module_does_not_vote() -> None:
    history = _history()
    history["stats"] = history["stats"][:3]

    result = build_full_alignment(_confluence(), history)

    assert result["agreeing"] == 3
    assert result["unanimous"] is False
