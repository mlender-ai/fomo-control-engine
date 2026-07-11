from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.db.repository import SQLiteRepository
from app.services import http_handlers


def test_calibration_get_returns_preparing_without_writes(client: TestClient, monkeypatch) -> None:
    def fail_write(*_args, **_kwargs):
        raise AssertionError("GET attempted a database write")

    monkeypatch.setattr(http_handlers.repository, "add_calibration_suggestion", fail_write)
    monkeypatch.setattr(http_handlers.repository, "upsert_calibration_report_cache", fail_write)

    response = client.get("/api/review/calibration")

    assert response.status_code == 200
    assert response.json()["cache_status"] == "preparing"
    assert response.json()["sample_warning"] == "집계 준비 중 · 워커 실행 대기"


def test_calibration_get_reads_cached_payload_without_writes(client: TestClient, monkeypatch) -> None:
    http_handlers.repository.upsert_calibration_report_cache(
        "calibration",
        {
            **http_handlers._calibration_preparing_payload(),
            "totals": {"total": 12, "tested": 10, "accuracy_pct": 60.0},
            "sample_warning": "N=10",
        },
    )

    def fail_write(*_args, **_kwargs):
        raise AssertionError("GET attempted a database write")

    monkeypatch.setattr(http_handlers.repository, "add_calibration_suggestion", fail_write)
    monkeypatch.setattr(http_handlers.repository, "upsert_calibration_report_cache", fail_write)

    response = client.get("/api/review/calibration")

    assert response.status_code == 200
    assert response.json()["cache_status"] == "ready"
    assert response.json()["computed_at"]
    assert response.json()["totals"]["tested"] == 10


def test_worker_refresh_builds_all_review_caches(client: TestClient) -> None:
    result = http_handlers.refresh_calibration_report_cache()

    assert result["computed"] == 3
    assert client.get("/api/review/calibration").json()["cache_status"] == "ready"
    assert client.get("/api/review/calibration/weekly").json()["cache_status"] == "ready"
    assert client.get("/api/performance").json()["cache_status"] == "ready"


def test_sqlite_calibration_cache_serializes_datetimes(tmp_path) -> None:
    repository = SQLiteRepository(str(tmp_path / "calibration-cache.db"))

    repository.upsert_calibration_report_cache(
        "calibration",
        {"generated_at": datetime(2026, 7, 11, 3, 0, tzinfo=timezone.utc)},
    )

    cached = repository.get_calibration_report_cache("calibration")
    assert cached is not None
    assert cached["payload"]["generated_at"] == "2026-07-11T03:00:00+00:00"
