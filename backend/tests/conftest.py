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


@pytest.fixture(autouse=True)
def isolated_alert_delivery_marker(tmp_path, monkeypatch):
    """발송 마커도 같은 이유로 격리한다 (2026-09-15).

    `notification_state.json` 만 막혀 있었고 `alert_delivery.json` 은 열려 있었다.
    기본 경로가 `../logs/alert_delivery.json` 이라, 기본 `Settings()` 로 AlertEngine 을
    만드는 테스트가 **실파일을 테스트 시각으로 덮어썼다.**

    실제로 그랬다: 게이트를 한 번 돌 때마다 마커가 `2026-07-08T12:00:00` 으로 덮였고,
    그 파일을 읽는 것은 **워커 밖의 침묵 감시자**(`deadman.sh`)다. 감시자가 "1643시간
    침묵"을 보게 되어 진짜 침묵과 구분할 수 없게 됐다.

    격리 대상이 하나 빠지면 감시가 거짓 데이터를 읽는다 — 목록이 아니라 규칙이어야 한다.
    """
    isolated_path = str(tmp_path / "alert_delivery.json")
    monkeypatch.setenv("FCE_ALERT_DELIVERY_PATH", isolated_path)
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "alert_delivery_path", isolated_path)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)
