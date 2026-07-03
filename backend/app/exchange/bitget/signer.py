import base64
import hashlib
import hmac
import time


def get_timestamp_ms() -> str:
    return str(int(time.time() * 1000))


def sign_bitget_request(
    secret: str,
    timestamp: str,
    method: str,
    request_path: str,
    query_string: str | None = None,
    body: str = "",
) -> str:
    method = method.upper()
    if query_string:
        message = f"{timestamp}{method}{request_path}?{query_string}{body}"
    else:
        message = f"{timestamp}{method}{request_path}{body}"

    digest = hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")

