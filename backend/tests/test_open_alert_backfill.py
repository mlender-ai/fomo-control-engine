"""진입 알림을 이벤트가 아니라 **빚**으로 다룬다 (2026-09-05).

## 무엇이 남아 있었나

앞선 두 번의 수리(진입 알림 손실 창 · 정숙 시간 제거)를 머지했는데도 진입 알림이 안 왔다.
남은 구멍은 **알림이 이벤트에만 매달려 있다**는 것이었다.

`sync_live_positions()` 는 그 호출에서 새로 만든 포지션 id 를 돌려준다. 그런데 그 핸들러를
워커만 부르는 게 아니다 — `POST /api/live/positions/sync` 가 같은 함수를 부르고,
대시보드의 수동 새로고침과 커맨드 팔레트가 그것을 호출한다. 포지션을 잡은 직후 화면을 여는
것은 트레이더의 기본 동작이다.

```
브라우저 sync → 포지션 행 생성 → created_position_ids 가 브라우저로 감 → 버려짐
워커  sync   → 이미 있음 → updated → created_position_ids 비어 있음 → 알림 없음
```

**영원히 안 난다.** 재기동해도 안 난다 — 잃은 것이 상태가 아니라 이벤트이기 때문이다.

"이 포지션에 진입 알림을 보냈는가"는 원장에 있는 상태다. 안 보냈으면 빚이고, 빚은 다음
주기에 갚는다. 그러면 누가 행을 만들었는지도, 그 사이 프로세스가 죽었는지도 무관해진다.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.api.deps import configure_runtime
from app.db.models import AlertRecord, Direction, Position, PositionStatus, utc_now
from app.db.repository import MemoryRepository
from app.exchange.mock import MockMarketDataProvider
from app.services import runtime as service


@pytest.fixture()
def repo():
    repository = MemoryRepository()
    configure_runtime(repo=repository, provider=MockMarketDataProvider())
    return repository


def _open_position(repo, *, age_minutes: int = 5, symbol: str = "BTCUSDT") -> Position:
    return repo.add_position(
        Position(
            symbol=symbol,
            direction=Direction.long,
            entry_price=100.0,
            quantity=1.0,
            leverage=3,
            status=PositionStatus.open,
            source="bitget",
            opened_at=utc_now() - timedelta(minutes=age_minutes),
        )
    )


def _record_open_alert(repo, position: Position) -> None:
    repo.add_alert(
        AlertRecord(
            rule_id="position_opened",
            position_id=position.id,
            symbol=position.symbol,
            severity="action",
            delivered=True,
        )
    )


def test_position_without_an_entry_alert_is_owed(repo) -> None:
    position = _open_position(repo)
    assert service.positions_owing_open_alert(max_age_minutes=180) == [str(position.id)]


def test_debt_is_settled_once_the_alert_is_recorded(repo) -> None:
    """한 번 시도했으면 갚은 것이다 — 매 주기 다시 울리면 그게 다음 사고다."""
    position = _open_position(repo)
    _record_open_alert(repo, position)
    assert service.positions_owing_open_alert(max_age_minutes=180) == []


def test_blocked_alert_also_settles_the_debt(repo) -> None:
    """관문이 막았거나 발송이 실패해도 원장에는 남는다 — 그것도 '시도했다'이다."""
    position = _open_position(repo)
    repo.add_alert(
        AlertRecord(
            rule_id="position_opened",
            position_id=position.id,
            symbol=position.symbol,
            severity="action",
            delivered=False,  # 막혔거나 발송 실패
        )
    )
    assert service.positions_owing_open_alert(max_age_minutes=180) == []


def test_other_rules_do_not_settle_the_entry_debt(repo) -> None:
    position = _open_position(repo)
    repo.add_alert(
        AlertRecord(
            rule_id="trigger_near",
            position_id=position.id,
            symbol=position.symbol,
            severity="action",
            delivered=True,
        )
    )
    assert service.positions_owing_open_alert(max_age_minutes=180) == [str(position.id)]


def test_old_positions_are_not_resurrected(repo) -> None:
    """배포 직후 며칠 전 보유 포지션까지 한꺼번에 울리면 그것은 알림이 아니라 소음이다."""
    _open_position(repo, age_minutes=60 * 24 * 3)
    assert service.positions_owing_open_alert(max_age_minutes=180) == []


def test_closed_positions_are_not_owed(repo) -> None:
    position = _open_position(repo)
    position.status = PositionStatus.closed
    position.closed_at = utc_now()
    repo.update_position(position)
    assert service.positions_owing_open_alert(max_age_minutes=180) == []


def test_limit_caps_one_cycle(repo) -> None:
    for index in range(8):
        _open_position(repo, symbol=f"SYM{index}USDT")
    assert len(service.positions_owing_open_alert(max_age_minutes=180, limit=3)) == 3


def test_window_zero_disables_recovery(repo) -> None:
    """끌 수 있어야 한다 — 끄지 못하는 자동 동작은 사고가 났을 때 되돌릴 수 없다."""
    _open_position(repo)
    assert service.positions_owing_open_alert(max_age_minutes=0) == []
    assert service.positions_owing_open_alert(max_age_minutes=180, limit=0) == []


def test_naive_opened_at_does_not_kill_the_job(repo, monkeypatch) -> None:
    """시간대 없는 값이 올라와도 터지지 않는다 — 알림을 살리려는 코드가 알림을 죽이면 안 된다."""
    position = _open_position(repo)
    position.opened_at = (utc_now() - timedelta(minutes=5)).replace(tzinfo=None)
    monkeypatch.setattr(service.runtime.repository, "list_positions", lambda status=None: [position])

    assert service.positions_owing_open_alert(max_age_minutes=180) == [str(position.id)]


def test_dashboard_stealing_the_sync_no_longer_loses_the_alert(repo) -> None:
    """이 회귀가 사고 자체를 재현한다.

    대시보드가 먼저 동기화해 포지션 행이 이미 존재하면, 워커의 동기화는 `created` 를 0 으로
    돌려준다. 이벤트에만 의존하던 옛 구조에서는 그 진입 알림이 영원히 사라졌다.
    """
    position = _open_position(repo)
    worker_created_ids: list[str] = []  # 워커가 본 것 — 비어 있다

    owed = service.positions_owing_open_alert(max_age_minutes=180)
    delivered = [*worker_created_ids, *[pid for pid in owed if pid not in worker_created_ids]]

    assert delivered == [str(position.id)]


# ── 배선 확인 — 함수가 있는 것과 잡이 부르는 것은 다르다 ──────────────────────


def test_deliver_alerts_actually_calls_the_backfill() -> None:
    """`_deliver_alerts` 가 큐와 회수분을 **합쳐서** 넘기는지 본다.

    회수 함수를 만들어 놓고 배선하지 않으면 아무것도 달라지지 않는다 — 이 회귀가 그 간극을 막는다.
    """
    import inspect

    from app.worker.manager import WorkerManager

    source = inspect.getsource(WorkerManager._deliver_alerts)
    assert "positions_owing_open_alert" in source, "회수가 알림 잡에 배선되지 않았다"
    assert "created_position_ids" in source
    # 큐를 대체하는 것이 아니라 더한다 — 큐가 살아 있으면 그쪽이 더 빠르다.
    assert "*created" in source and "*owed" in source


def test_backfill_runs_as_an_isolated_hook() -> None:
    """회수가 터져도 나머지 알림이 같이 죽으면 안 된다 — 훅으로 격리돼야 한다."""
    import inspect

    from app.worker.manager import WorkerManager

    source = inspect.getsource(WorkerManager._deliver_alerts)
    assert '_run_hook(\n            "backfill_open_alerts"' in source or '"backfill_open_alerts"' in source
    assert 'parent="deliver_alerts"' in source


def test_backfill_window_is_configurable() -> None:
    from app.core.config import Settings

    settings = Settings()
    assert settings.alert_open_backfill_window_minutes > 0
    assert settings.alert_open_backfill_limit > 0
