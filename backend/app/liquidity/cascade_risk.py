def cascade_risk_label(magnitude: float, distance_pct: float) -> str:
    pressure = magnitude / max(abs(distance_pct), 0.5)
    if pressure >= 30:
        return "high"
    if pressure >= 14:
        return "moderate"
    return "low"
