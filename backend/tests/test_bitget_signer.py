from app.exchange.bitget.signer import sign_bitget_request


def test_sign_bitget_request_without_query_is_deterministic() -> None:
    signature = sign_bitget_request(
        secret="unit-test-secret",
        timestamp="16273667805456",
        method="GET",
        request_path="/api/v2/mix/position/all-position",
    )

    assert signature == sign_bitget_request(
        secret="unit-test-secret",
        timestamp="16273667805456",
        method="get",
        request_path="/api/v2/mix/position/all-position",
    )
    assert signature


def test_sign_bitget_request_with_query_changes_signature() -> None:
    without_query = sign_bitget_request(
        secret="unit-test-secret",
        timestamp="16273667805456",
        method="GET",
        request_path="/api/v2/mix/position/all-position",
    )
    with_query = sign_bitget_request(
        secret="unit-test-secret",
        timestamp="16273667805456",
        method="GET",
        request_path="/api/v2/mix/position/all-position",
        query_string="productType=USDT-FUTURES&marginCoin=USDT",
    )

    assert with_query != without_query
    assert with_query == sign_bitget_request(
        secret="unit-test-secret",
        timestamp="16273667805456",
        method="GET",
        request_path="/api/v2/mix/position/all-position",
        query_string="productType=USDT-FUTURES&marginCoin=USDT",
    )

