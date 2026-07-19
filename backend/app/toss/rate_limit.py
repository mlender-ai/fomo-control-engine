from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


@dataclass
class TokenBucket:
    rate: float
    capacity: float
    tokens: float | None = None
    updated_at: float | None = None

    def __post_init__(self) -> None:
        self.tokens = self.capacity if self.tokens is None else self.tokens
        self.updated_at = time.monotonic() if self.updated_at is None else self.updated_at
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        assert self.updated_at is not None and self.tokens is not None
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = max(0.0, now - float(self.updated_at))
                self.tokens = min(self.capacity, float(self.tokens) + elapsed * self.rate)
                self.updated_at = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                delay = (1 - self.tokens) / self.rate
            await asyncio.sleep(delay)

    def slow(self, factor: float = 0.5) -> None:
        self.rate = max(0.1, self.rate * factor)
