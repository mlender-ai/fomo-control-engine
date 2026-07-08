from __future__ import annotations

from dataclasses import dataclass

ALLOWED_CALLBACK_ACTIONS = {
    "detail",
    "plan",
    "insight",
    "flow",
    "brief",
    "chart",
    "regen_insight",
    "refresh",
    "list",
    "one_liners",
    "review",
    "scout",
    "status",
    "sim",
}


@dataclass(frozen=True)
class BotCallback:
    action: str
    symbol: str = ""


def encode_callback(action: str, symbol: str = "") -> str:
    safe_action = action if action in ALLOWED_CALLBACK_ACTIONS else "list"
    return f"v1:{safe_action}:{symbol.strip().upper()}"[:64]


def parse_callback(value: str | None) -> BotCallback | None:
    if not value:
        return None
    parts = value.split(":", 2)
    if len(parts) != 3 or parts[0] != "v1":
        return None
    action = parts[1]
    symbol = parts[2].strip().upper()
    if action not in ALLOWED_CALLBACK_ACTIONS:
        return None
    return BotCallback(action=action, symbol=symbol)
