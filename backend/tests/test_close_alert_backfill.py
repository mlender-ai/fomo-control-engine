"""2026-09-10 — 종료 알림 손실 창. 진입만 막고 종료는 열려 있었다.

## 무엇이 있었나

사용자가 포지션을 정리했는데 텔레그램이 오지 않았다. 워커는 정상이었다 —
34회 순환, 굶음 0, error 0, `loop_lag` 1.48초.

원인은 **손실 창의 나머지 절반**이었다. 진입 알림은 빚으로 회수하게 만들었지만
(`positions_owing_open_alert`) 종료는 그대로 이벤트였다:

    브라우저 sync → 확정 틱 충족 → 포지션 닫힘 → closed_positions 가 브라우저로 감 → 버려짐
    워커  sync   → 이미 closed → 부재 목록에 없음 → 알릴 것이 없다

**포지션을 정리한 직후 화면을 새로고침하는 것은 트레이더의 기본 동작이다.** 대시보드의
수동 새로고침과 커맨드 팔레트가 같은 핸들러를 부르므로 그 클릭 하나가 종료 알림을 삼켰다.

실측: 창 밖에 종료 알림을 못 받은 포지션이 **18건** 쌓여 있었다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.db.models import PositionStatus
from app.services import runtime as runtime_module


class _Repo:
    def __init__(self, positions, alerts_by_position=None):
        self._positions = positions
        self._alerts = alerts_by_position or {}

    def list_positions(self, status=None):
        if status is None:
            return list(self._positions)
        return [p for p in self._positions if p.status == status]

    def list_alerts(self, position_id, limit=50):
        return self._alerts.get(position_id, [])


def _position(*, status=PositionStatus.closed, closed_at=None, symbol="BTCUSDT"):
    return SimpleNamespace(
        id=uuid4(),
        symbol=symbol,
        status=status,
        closed_at=closed_at,
        model_dump=lambda mode="json": {"id": "x", "symbol": symbol, "status": status.value},
    )


@pytest.fixture
def patched(monkeypatch):
    def _apply(repo):
        monkeypatch.setattr(runtime_module.runtime, "repository", repo, raising=False)

    return _apply


def _now():
    return datetime.now(timezone.utc)


def test_a_closed_position_without_a_close_alert_is_a_debt(patched) -> None:
    """**이것이 사용자가 못 받은 그 알림이다.**"""
    position = _position(closed_at=_now() - timedelta(minutes=5))
    patched(_Repo([position]))
    rows = runtime_module.positions_owing_close_alert(max_age_minutes=180)
    assert len(rows) == 1
    assert rows[0]["position"]["symbol"] == "BTCUSDT"


def test_a_position_that_already_alerted_is_not_a_debt(patched) -> None:
    """같은 종료를 두 번 알리면 신뢰가 깨진다."""
    position = _position(closed_at=_now() - timedelta(minutes=5))
    patched(_Repo([position], {position.id: [SimpleNamespace(rule_id="position_closed")]}))
    assert runtime_module.positions_owing_close_alert(max_age_minutes=180) == []


def test_other_alerts_on_the_position_do_not_count_as_a_close_alert(patched) -> None:
    """`take_profit_hit` 이 왔다고 종료를 알린 것이 아니다."""
    position = _position(closed_at=_now() - timedelta(minutes=5))
    patched(_Repo([position], {position.id: [SimpleNamespace(rule_id="take_profit_hit")]}))
    assert len(runtime_module.positions_owing_close_alert(max_age_minutes=180)) == 1


def test_old_closures_are_outside_the_window(patched) -> None:
    """배포 직후 며칠 전 종료분이 한꺼번에 울리면 안 된다 — 실측 18건이 있었다."""
    position = _position(closed_at=_now() - timedelta(days=3))
    patched(_Repo([position]))
    assert runtime_module.positions_owing_close_alert(max_age_minutes=180) == []


def test_needs_exit_record_is_also_a_debt(patched) -> None:
    """종료 기록이 미완이어도 **닫힌 사실**은 알려야 한다."""
    position = _position(status=PositionStatus.needs_exit_record, closed_at=_now() - timedelta(minutes=5))
    patched(_Repo([position]))
    assert len(runtime_module.positions_owing_close_alert(max_age_minutes=180)) == 1


def test_open_positions_are_never_close_debts(patched) -> None:
    position = _position(status=PositionStatus.open, closed_at=None)
    patched(_Repo([position]))
    assert runtime_module.positions_owing_close_alert(max_age_minutes=180) == []


def test_missing_closed_at_is_skipped_not_crashed(patched) -> None:
    """`closed_at` 이 없는 행에서 터지면 알림 잡이 죽는다."""
    patched(_Repo([_position(closed_at=None)]))
    assert runtime_module.positions_owing_close_alert(max_age_minutes=180) == []


def test_naive_closed_at_does_not_raise(patched) -> None:
    """시간대 없는 값 비교가 TypeError 로 터지면 **알림을 살리려는 코드가 알림을 죽인다.**"""
    position = _position(closed_at=datetime.now() - timedelta(minutes=5))
    patched(_Repo([position]))
    assert len(runtime_module.positions_owing_close_alert(max_age_minutes=180)) == 1


def test_limit_caps_one_cycle(patched) -> None:
    positions = [_position(closed_at=_now() - timedelta(minutes=i + 1), symbol=f"S{i}") for i in range(9)]
    patched(_Repo(positions))
    assert len(runtime_module.positions_owing_close_alert(max_age_minutes=180, limit=3)) == 3


@pytest.mark.parametrize("window,limit", [(0, 5), (180, 0), (-1, -1)])
def test_disabled_by_either_bound(patched, window: int, limit: int) -> None:
    """**끄지 못하는 자동 동작은 사고가 났을 때 되돌릴 수 없다.**"""
    patched(_Repo([_position(closed_at=_now())]))
    assert runtime_module.positions_owing_close_alert(max_age_minutes=window, limit=limit) == []


def test_the_worker_merges_the_debt_without_duplicating_the_queue() -> None:
    """큐에 이미 있는 종료를 빚으로 또 넣으면 알림이 두 번 나간다."""
    import pathlib

    src = pathlib.Path(runtime_module.__file__).parents[1].joinpath("worker/manager.py").read_text()
    body = src.split("async def _deliver_alerts")[1].split("\n    async def ")[0]
    assert "positions_owing_close_alert" in body
    assert "queued_ids" in body, "큐와 빚을 중복 제거하지 않는다"


def test_close_backfill_is_registered_as_a_job() -> None:
    """등록하지 않으면 하트비트에 안 잡히고, 실패가 조용해진다."""
    import pathlib

    src = pathlib.Path(runtime_module.__file__).parents[1].joinpath("worker/manager.py").read_text()
    assert '"backfill_close_alerts": WorkerJob(' in src


def test_close_backfill_is_isolated_in_a_hook() -> None:
    """회수가 터져도 나머지 알림이 같이 죽으면 안 된다."""
    import pathlib

    src = pathlib.Path(runtime_module.__file__).parents[1].joinpath("worker/manager.py").read_text()
    assert '"backfill_close_alerts",' in src
    assert 'parent="deliver_alerts"' in src
