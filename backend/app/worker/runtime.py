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
                "sync_positions": {"status": "not_started"},
                "refresh_market_data": {"status": "not_started"},
                "collect_derivatives": {"status": "not_started"},
                "regen_stale_insights": {"status": "not_started"},
                "database_retention": {"status": "not_started"},
                "database_backup": {"status": "not_started"},
                "detect_closures": {"status": "not_started"},
                "evaluate_alerts": {"status": "not_started"},
                "evaluate_performance_alerts": {"status": "not_started"},
                "daily_summary": {"status": "not_started"},
                "weekly_calibration_report": {"status": "not_started"},
                "refresh_calibration_cache": {"status": "not_started"},
                "refresh_symbol_catalog": {"status": "not_started"},
                "interim_scoring": {"status": "not_started"},
                "alert_response_scoring": {"status": "not_started"},
                "scout_scan": {"status": "not_started"},
                "polymarket_paper": {"status": "not_started"},
                "telegram_bot": {"status": "not_started"},
            },
            "notifications": {"muted_until": None, "is_muted": False},
        }
    return _worker_manager.status()
