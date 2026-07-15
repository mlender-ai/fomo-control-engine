from datetime import datetime, timezone

import httpx

from app.marketdata.assets import classify_asset_class
from app.marketdata.occ_options import fetch_occ_options_summary, parse_series_search, parse_volume_query


SERIES_SAMPLE = """Series Search Results for SOXL
ProductSymbol\t\tyear\tMonth\tDay\tInteger\tDec\tC/P\tCall\tPut\tPosition Limit
SOXL\t\t2026\t07\t17\t25\t000\tC P\t3\t5813\t25000000
SOXL\t\t2026\t07\t17\t30\t500\tC P\t120\t40\t25000000
2SOXL\t\t2026\t07\t17\t25\t000\tC P\t999\t999\t25000000
SOXL\t\t2026\t07\t10\t20\t000\tC P\t50\t50\t25000000
"""

VOLUME_SAMPLE = """quantity,underlying,symbol,actype,porc,exchange,actdate
100,SOXL,SOXL,C,C,CBOE,07/14/2026,
250,SOXL,SOXL,M,P,CBOE,07/14/2026,
999,SOXL,2SOXL,C,P,CBOE,07/14/2026,
"""


def test_soxl_is_classified_as_stock_rwa_ticker() -> None:
    assert classify_asset_class("SOXLUSDT") == "stock"


def test_series_parser_excludes_adjusted_and_expired_contracts() -> None:
    rows = parse_series_search(SERIES_SAMPLE, "SOXL", as_of=datetime(2026, 7, 15, tzinfo=timezone.utc).date())

    assert rows == [
        {"expiry": "2026-07-17", "strike": 25.0, "call_open_interest": 3, "put_open_interest": 5813},
        {"expiry": "2026-07-17", "strike": 30.5, "call_open_interest": 120, "put_open_interest": 40},
    ]


def test_volume_parser_sums_exact_symbol_and_keeps_basis_date() -> None:
    assert parse_volume_query(VOLUME_SAMPLE, "SOXL") == {
        "call_volume": 100,
        "put_volume": 250,
        "volume_date": "2026-07-14",
    }


def test_fetch_summary_combines_oi_and_volume_without_api_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/series-search":
            return httpx.Response(200, text=SERIES_SAMPLE)
        if request.url.path == "/volume-query":
            return httpx.Response(200, text=VOLUME_SAMPLE)
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        summary = fetch_occ_options_summary(
            "SOXLUSDT",
            client=client,
            now=datetime(2026, 7, 15, 15, tzinfo=timezone.utc),
        )

    assert summary["source"] == "occ_public"
    assert summary["call_open_interest"] == 123
    assert summary["put_open_interest"] == 5853
    assert summary["put_call_oi_ratio"] == 47.5854
    assert summary["call_volume"] == 100
    assert summary["put_volume"] == 250
    assert summary["put_call_volume_ratio"] == 2.5
    assert summary["volume_date"] == "2026-07-14"
    assert "관측 전용" in summary["notes"][1]
