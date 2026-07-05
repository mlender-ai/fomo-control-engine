from __future__ import annotations


class ChatGuard:
    def __init__(self, allowed_chat_ids: list[int]) -> None:
        self.allowed_chat_ids = set(allowed_chat_ids)

    def is_allowed(self, chat_id: int | None) -> bool:
        return chat_id is not None and chat_id in self.allowed_chat_ids

