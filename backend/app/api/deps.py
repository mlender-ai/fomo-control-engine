from __future__ import annotations

from app.services import http_handlers as handlers
from app.db.repository import Repository
from app.exchange.base import MarketDataProvider


def configure_runtime(repo: Repository | None = None, provider: MarketDataProvider | None = None) -> None:
    handlers.configure_runtime(repo=repo, provider=provider)


def get_repository() -> Repository:
    return handlers.repository


def get_market_provider() -> MarketDataProvider:
    return handlers.market_provider
