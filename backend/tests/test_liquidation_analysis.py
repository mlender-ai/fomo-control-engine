from app.exchange.mock import MockMarketDataProvider
from app.liquidity.liquidation_clusters import analyze_liquidation
from app.report.engine import generate_report


def test_liquidation_cluster_asymmetry_calculation() -> None:
    report = generate_report(MockMarketDataProvider().get_snapshot("BTCUSDT"))
    analysis = analyze_liquidation(report)

    assert analysis.upper_clusters
    assert analysis.lower_clusters
    assert 0 <= analysis.asymmetry_score <= 100
