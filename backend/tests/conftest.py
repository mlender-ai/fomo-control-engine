import pytest
from fastapi.testclient import TestClient

from app.api.routes import configure_runtime
from app.db.repository import MemoryRepository
from app.exchange.mock import MockMarketDataProvider
from app.main import app


@pytest.fixture(autouse=True)
def isolated_runtime():
    configure_runtime(repo=MemoryRepository(), provider=MockMarketDataProvider())
    yield
    configure_runtime(repo=MemoryRepository(), provider=MockMarketDataProvider())


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)

