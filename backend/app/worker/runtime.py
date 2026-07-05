from __future__ import annotations

from typing import Any

_worker_manager = None


def set_worker_manager(manager) -> None:
    global _worker_manager
    _worker_manager = manager


def get_worker_status() -> dict[str, Any]:
    if _worker_manager is None:
        return {
            "status": "not_started",
            "jobs": {
                "position_sync": {"status": "not_started"},
                "daily_summary": {"status": "not_started"},
                "telegram_bot": {"status": "not_started"},
            },
            "notifications": {"muted_until": None, "is_muted": False},
        }
    return _worker_manager.status()
