import pytest
from fastapi.testclient import TestClient

from app.api.deps import configure_runtime
from app.db.repository import MemoryRepository
from app.exchange.mock import MockMarketDataProvider
from app.main import app


@pytest.fixture(autouse=True)
def isolated_runtime():
    configure_runtime(repo=MemoryRepository(), provider=MockMarketDataProvider())
    yield
    configure_runtime(repo=MemoryRepository(), provider=MockMarketDataProvider())


@pytest.fixture(autouse=True)
def isolated_notification_state(tmp_path, monkeypatch):
    """테스트가 실서비스 notification_state.json을 오염시키지 않게 격리 (WO-44).

    기본 경로가 backend cwd 상대라, 테스트에서 기본 Settings()로 AlertEngine/
    WorkerManager를 만들면 실파일을 테스트 상태(null)로 덮어써 재시작 직후
    펄스가 중복 재발화하는 실사고가 있었다.
    """
    isolated_path = str(tmp_path / "notification_state.json")
    # 새로 생성되는 Settings() (워커 테스트 등).
    monkeypatch.setenv("FCE_NOTIFICATION_STATE_PATH", isolated_path)
    # 이미 lru_cache된 전역 설정 (TestClient/http_handlers 경로).
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "notification_state_path", isolated_path)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)
