"""일일 요약 트리거 회귀 (WO-FCE-ENTRY-THROUGHPUT-01 후속).

과거 트리거는 `strftime("%H:%M") == target` 즉 **분 단위 정확 일치**였다. 그 1분 틱을
한 번 놓치면 그날 요약이 통째로 사라졌고 발송 실패 기록도 남지 않았다
(2026-08-04 실측: 잡은 정상 실행 중인데 last_summary_date 가 08-03 에 멈춰 있었다).

침묵이 스스로를 은폐하지 않도록 **놓친 틱을 따라잡는다**.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.core.config import Settings
from app.notify.rules import morning_summary_due

KST = ZoneInfo("Asia/Seoul")


def _settings(target: str = "08:30") -> Settings:
    return Settings(telegram_daily_summary_time=target, telegram_quiet_hours_timezone="Asia/Seoul")


def _at(hour: int, minute: int, second: int = 30) -> datetime:
    return datetime(2026, 8, 4, hour, minute, second, tzinfo=KST).astimezone(timezone.utc)


def test_not_due_before_target() -> None:
    due, date_key = morning_summary_due(_settings(), "2026-08-03", _at(8, 29))
    assert due is False
    assert date_key == "2026-08-04"


def test_due_at_target_minute() -> None:
    due, _ = morning_summary_due(_settings(), "2026-08-03", _at(8, 30))
    assert due is True


def test_catches_up_after_missed_tick() -> None:
    """이 테스트가 이 수리의 핵심 — 08:30 을 놓쳤어도 이후 틱에서 따라잡아야 한다."""
    for hour, minute in ((8, 31), (9, 0), (12, 0), (21, 17), (23, 59)):
        due, _ = morning_summary_due(_settings(), "2026-08-03", _at(hour, minute))
        assert due is True, f"{hour:02d}:{minute:02d} 에서 따라잡지 못했다"


def test_only_once_per_day() -> None:
    """하루 1회 상한은 last_summary_date 가 보장한다 — 따라잡기가 스팸이 되면 안 된다."""
    for hour, minute in ((8, 30), (9, 0), (21, 17)):
        due, _ = morning_summary_due(_settings(), "2026-08-04", _at(hour, minute))
        assert due is False


def test_never_sent_still_due_after_target() -> None:
    due, _ = morning_summary_due(_settings(), None, _at(10, 0))
    assert due is True


def test_invalid_target_does_not_fire() -> None:
    due, _ = morning_summary_due(_settings("not-a-time"), "2026-08-03", _at(12, 0))
    assert due is False


def test_date_key_uses_configured_timezone() -> None:
    """KST 00:30 은 UTC 로는 전날이다 — 날짜 키가 UTC 로 밀리면 하루가 겹치거나 빠진다."""
    _, date_key = morning_summary_due(_settings(), None, _at(0, 30))
    assert date_key == "2026-08-04"
