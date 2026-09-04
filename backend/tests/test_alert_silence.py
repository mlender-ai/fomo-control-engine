"""WO-FCE-ALERT-SILENCE-01 — 알림이 통째로 죽는 구조를 끝낸다.

## 무엇이 반복됐나

`27b6e11` 이 기록한 사고가 구조 때문이다:

```
sync_positions ── 진입 알림 · 정기 펄스 · 구조 알림 · 무효화 경보
```

**단일 실패점이었다.** 이 잡이 450초 타임아웃으로 죽으면 알림 전체가 같이 죽었고 그 전력이
3회 이상이다 — 9/1 15시간, 이번 8시간 43분.

그리고 **죽었다는 사실을 알릴 경로도 없었다.** 생존·사망 신호는 사용자 지시로 강등돼 있고
(2026-08-16), 그 강등은 타당하다. 그래서 강등을 되돌리는 대신 **다른 신호**를 만든다.

이 파일이 고정하는 명제:

1. 알림이 `sync_positions` 와 **분리**돼 있다 (3-1)
2. 동기화가 죽어도 알림은 **낡음을 표기하고** 계속 간다
3. 훅이 부모 예산을 **나눠** 쓴다 — 타임아웃을 올리지 않는다 (3-3 · C4)
4. 침묵 감지가 **워커 밖**에 있다 (3-2)
5. 침묵 감지가 **강등 게이트를 지나지 않는다** — 다른 신호다 (C1)
"""

from __future__ import annotations

import ast
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MANAGER = (REPO_ROOT / "backend/app/worker/manager.py").read_text(encoding="utf-8")
DEADMAN = (REPO_ROOT / "scripts/local/deadman.sh").read_text(encoding="utf-8")


def _code_only(source: str) -> str:
    """주석을 뗀 실행 코드. **이름을 설명하는 것과 쓰는 것은 다르다** — 주석에서 어떤
    게이트를 "지나지 않는다"고 적으면 그 이름이 본문에 남고, 부분 문자열 검사가 그것을
    위반으로 읽는다. 이 프로젝트에서 세 번 겪었다."""
    return "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))


# C3 — 판정·진입·출구 로직은 한 줄도 바뀌지 않는다.
UNTOUCHABLE = ("backend/app/analyst", "backend/app/structure", "backend/app/paper/policy.py")


# ── 3-1 알림 분리 ───────────────────────────────────────────────────────


def test_alerts_are_a_separate_scheduled_job() -> None:
    """**단일 실패점을 없앤다.** 알림이 동기화 잡 안에서 돌면 그 잡과 함께 죽는다."""
    assert '"deliver_alerts": WorkerJob(' in MANAGER
    sync = MANAGER.split("async def _sync_positions")[1].split("\n    def _alert_payload")[0]
    for alert_hook in ("evaluate_lifecycle", "periodic_pulse", "evaluate_structure_context", "daily_summary"):
        assert alert_hook not in sync, f"알림 훅이 아직 동기화 잡 안에 있다: {alert_hook}"


def test_alert_job_carries_every_hook_that_moved() -> None:
    """옮긴 것이지 뺀 것이 아니다 — 훅이 사라지면 그 알림이 영영 안 온다."""
    deliver = MANAGER.split("async def _deliver_alerts")[1].split("\n    def _queue_lifecycle")[0]
    for hook in (
        "evaluate_lifecycle",
        "evaluate_alerts",
        "evaluate_structure_context",
        "evaluate_performance_alerts",
        "periodic_pulse",
        "daily_summary",
    ):
        assert hook in deliver, f"알림 훅이 이사 중에 사라졌다: {hook}"


def test_alert_job_does_not_call_the_sync_runner() -> None:
    """알림이 동기화를 호출하면 결합이 되살아난다."""
    deliver = MANAGER.split("async def _deliver_alerts")[1].split("\n    def _queue_lifecycle")[0]
    assert "sync_and_analyze" not in deliver
    assert "_alert_payload()" in deliver


class _Manager:
    """`_alert_payload` 만 흉내낸다 — 워커 전체를 세우지 않고 계약을 검사한다."""

    def __init__(self, settings, payload, last_sync_at) -> None:
        self.settings = settings
        self._last_sync_payload = payload
        self._last_sync_at = last_sync_at

    _alert_payload = None  # 아래에서 실제 구현을 붙인다


def _payload_for(payload, last_sync_at):
    from app.core.config import Settings
    from app.worker.manager import WorkerManager

    manager = _Manager(Settings(), payload, last_sync_at)
    return WorkerManager._alert_payload(manager)


def test_alerts_still_run_when_sync_never_succeeded() -> None:
    """**동기화가 죽어도 알림은 산다.** 이것이 3-1 의 핵심 수용 기준이다."""
    payload = _payload_for(None, None)

    assert payload["sync_stale"] is True
    assert payload["sync_age_seconds"] is None
    assert "아직 없다" in payload["sync_stale_note"]
    assert payload["positions"] == []


def test_stale_sync_is_labelled_not_silent() -> None:
    """낡았으면 **낡았다고 알린다**(3-1 항목 2). 침묵하지 않는다."""
    old = datetime.now(timezone.utc) - timedelta(hours=3)
    payload = _payload_for({"positions": [{"symbol": "BTCUSDT"}]}, old)

    assert payload["sync_stale"] is True
    assert payload["sync_age_seconds"] > 3600
    assert "갱신되지 않았다" in payload["sync_stale_note"]
    # 낡아도 데이터는 넘긴다 — 침묵보다 낡은 값이 낫고, 낡았다는 라벨이 함께 간다.
    assert payload["positions"] == [{"symbol": "BTCUSDT"}]


def test_fresh_sync_is_not_flagged() -> None:
    """대조 — 정상일 때 낡음 표기가 붙으면 그것이 오탐이다."""
    payload = _payload_for({"positions": []}, datetime.now(timezone.utc))

    assert payload["sync_stale"] is False
    assert "sync_stale_note" not in payload


def test_lifecycle_events_are_delivered_once() -> None:
    """낡은 페이로드를 매 틱 다시 읽으면 같은 진입이 반복 판정된다.

    큐로 바뀐 뒤에도 같다 — 꺼낸 것은 큐에서 빠진다.
    """
    from app.worker.manager import WorkerManager

    queue = _Queue()
    queue.sync(["a"])

    assert WorkerManager._drain_lifecycle(queue)[0] == ["a"]
    assert WorkerManager._drain_lifecycle(queue)[0] == [], "같은 진입이 두 번 전달된다"


# ── 라이프사이클 큐 — 분리가 만든 손실 창을 막는다 ─────────────────────


class _Queue:
    """큐 헬퍼만 쓴다 — 워커 전체를 세우지 않고 손실 여부를 검사한다."""

    def __init__(self) -> None:
        from app.core.config import Settings

        self.settings = Settings()
        self._last_sync_payload = None
        self._last_sync_at = None
        self._pending_created: list[str] = []
        self._pending_closed: list[dict] = []
        self._lifecycle_dropped = 0

    def sync(self, ids: list[str]) -> None:
        """`_sync_positions` 가 하는 그대로 — 페이로드를 통째로 교체하고 큐에 쌓는다."""
        from app.worker.manager import WorkerManager

        payload = {"positions": [], "created_position_ids": list(ids), "closed_positions": []}
        self._last_sync_payload = payload
        self._last_sync_at = datetime.now(timezone.utc)
        WorkerManager._queue_lifecycle(self, payload)

    def drain(self) -> list[str]:
        from app.worker.manager import WorkerManager

        return WorkerManager._drain_lifecycle(self)[0]


def test_entry_survives_a_skipped_alert_tick() -> None:
    """**PR #28 이 만든 손실 창.**

    알림을 독립 잡으로 뺐지만 진입 목록을 페이로드에 얹어 뒀다. 두 잡이 같은 주기로 돌고
    순서 보장이 없으므로, 알림이 한 틱만 늦으면(락 점유·타임아웃) 그 사이 동기화가
    페이로드를 **통째로 교체**해 진입이 사라졌다.
    """
    queue = _Queue()
    queue.sync(["POS-2"])
    queue.sync(["POS-3"])

    assert queue.drain() == ["POS-2", "POS-3"], "알림이 한 틱 늦은 사이 진입이 사라졌다"


def test_an_empty_sync_does_not_erase_a_pending_entry() -> None:
    """**가장 흔한 경로다.** 동기화는 90초마다 돌면서 대부분 빈 목록을 반환한다 —
    진입이 없는 주기가 정상이기 때문이다. 그 빈 목록이 직전 진입을 지웠다."""
    queue = _Queue()
    queue.sync(["POS-4"])
    queue.sync([])

    assert queue.drain() == ["POS-4"], "빈 동기화가 대기 중인 진입을 지웠다"


def test_the_same_entry_is_not_queued_twice() -> None:
    """같은 진입이 두 번 큐에 들어가면 알림이 두 번 나간다."""
    queue = _Queue()
    queue.sync(["POS-5"])
    queue.sync(["POS-5"])

    assert queue.drain() == ["POS-5"]


def test_a_failed_delivery_puts_the_entry_back() -> None:
    """훅이 죽었는데 큐를 이미 비웠으면 그 진입은 영영 사라진다 — 수리가 새 손실을 만든다."""
    from app.worker.manager import WorkerManager

    queue = _Queue()
    queue.sync(["POS-6"])
    created, closed = WorkerManager._drain_lifecycle(queue)
    WorkerManager._requeue_lifecycle(queue, created, closed)

    assert queue.drain() == ["POS-6"]


def test_queue_overflow_is_counted_not_silent() -> None:
    """**조용히 버리는 것이 이 결함의 본체다.** 상한은 두되 버린 수를 센다."""
    from app.worker.manager import _LIFECYCLE_QUEUE_MAX

    queue = _Queue()
    for index in range(_LIFECYCLE_QUEUE_MAX + 100):
        queue.sync([f"P{index}"])

    assert len(queue._pending_created) == _LIFECYCLE_QUEUE_MAX
    assert queue._lifecycle_dropped == 100


def test_delivery_job_reports_queue_depth() -> None:
    """대기·유실이 조회되지 않으면 이 결함이 다시 침묵한다."""
    deliver = MANAGER.split("async def _deliver_alerts")[1].split("\n    def _queue_lifecycle")[0]

    assert "_drain_lifecycle()" in deliver, "알림 잡이 큐에서 꺼내지 않는다"
    assert "_requeue_lifecycle" in deliver
    for field in ("lifecycle_pending", "lifecycle_dropped", "lifecycle_delivered"):
        assert field in deliver, f"큐 상태가 결과에 없다: {field}"


def test_sync_queues_instead_of_overwriting() -> None:
    """페이로드에 얹어 두면 다음 동기화가 덮어쓴다 — 그것이 손실의 기전이었다."""
    sync = MANAGER.split("async def _sync_positions")[1].split("\n    def _alert_payload")[0]

    assert "_queue_lifecycle(payload)" in sync


# ── 3-3 훅 예산 분할 ────────────────────────────────────────────────────


def test_hook_budget_divides_and_never_raises() -> None:
    """**C4 — 타임아웃을 올려서 해결하지 않는다.** 나누는 것이므로 모든 값이 작아진다."""
    from app.core.config import Settings
    from app.worker.manager import WorkerManager

    manager = WorkerManager(Settings())
    for parent in ("sync_positions", "deliver_alerts"):
        parent_budget = manager._job_timeout_seconds(parent)
        hook_budget = manager._hook_budget(parent)
        assert hook_budget <= parent_budget, f"{parent}: 훅 예산이 부모보다 크다"


def test_hook_budget_is_never_below_the_configured_floor() -> None:
    """**나누되 조이지 않는다.**

    처음 구현은 `부모 ÷ 훅 수` 를 그대로 써서 `deliver_alerts` 훅을 450초→90초로 줄였다.
    `evaluate_lifecycle` 은 신규 포지션마다 분석을 조회하므로(심볼당 ~30초) 진입 3건이면
    그 자리에서 잘린다 — 알림을 살리려던 변경이 알림을 자르게 된다.

    하한은 `worker_job_timeout_floor_seconds` 다. 시스템이 이미 "이만큼은 받아야 조기
    취소되지 않는다"고 정한 값이며, 새 문턱을 만들지 않고 재사용한다.
    """
    from app.core.config import Settings
    from app.worker.manager import WorkerManager

    settings = Settings()
    manager = WorkerManager(settings)
    floor = int(settings.worker_job_timeout_floor_seconds)
    for parent in ("sync_positions", "deliver_alerts"):
        budget = manager._hook_budget(parent)
        assert budget >= min(floor, manager._job_timeout_seconds(parent)), f"{parent}: 훅 예산이 안전 하한 아래다"


def test_lifecycle_hook_can_analyse_several_new_positions() -> None:
    """진입이 여러 건일 때 분석 조회가 예산 안에 들어가는가 — 절단이 곧 침묵이다."""
    from app.core.config import Settings
    from app.worker.manager import WorkerManager

    manager = WorkerManager(Settings())
    # 실측 기준 심볼당 ~30초. 최소 3건은 처리할 수 있어야 한다.
    assert manager._hook_budget("deliver_alerts") >= 90


def test_hook_share_counts_match_the_actual_hooks() -> None:
    """어긋나면 예산이 다시 한 훅에 쏠린다."""
    from app.worker.manager import _HOOK_SHARES

    deliver = MANAGER.split("async def _deliver_alerts")[1].split("\n    def _queue_lifecycle")[0]
    assert deliver.count('parent="deliver_alerts"') == _HOOK_SHARES["deliver_alerts"]
    sync = MANAGER.split("async def _sync_positions")[1].split("\n    def _alert_payload")[0]
    assert sync.count('parent="sync_positions"') == _HOOK_SHARES["sync_positions"]


def test_timeout_settings_are_untouched() -> None:
    """C4 — 값 자체는 diff 0줄이다."""
    from app.core.config import Settings

    settings = Settings()
    assert settings.worker_job_timeout_multiplier == 5
    assert settings.worker_job_timeout_floor_seconds == 120
    assert settings.worker_job_timeout_ceiling_seconds == 1800

    diff = subprocess.run(["git", "diff", "origin/main", "--", "backend/app/core/config.py"], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if diff.returncode == 0:
        changed = [line for line in diff.stdout.splitlines() if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
        assert not any("timeout" in line.lower() for line in changed), f"타임아웃 설정이 변경됐다:\n{changed}"


# ── 3-2 침묵 감지 ───────────────────────────────────────────────────────


def test_worker_writes_a_delivery_marker_but_does_not_judge() -> None:
    """**감시자가 감시 대상 안에 살면 침묵이 스스로를 은폐한다.**

    워커는 발송 시각만 남기고 침묵 판정은 하지 않는다.
    """
    alerts = (REPO_ROOT / "backend/app/notify/alerts.py").read_text(encoding="utf-8")
    block = _code_only(alerts.split("def _touch_delivery_marker")[1].split("\n    def _record")[0])

    assert "last_delivered_at" in block
    # **판정 흔적이 없어야 한다.** WO 이름(`ALERT-SILENCE-01`)이 docstring 에 있는 것과
    # 판정하는 것은 다르므로, 이름이 아니라 **판정 구조**를 본다: 문턱 상수·비교·발송.
    for judgement in ("SILENCE_LIMIT", "silence_limit", "send_to_all", "sendMessage"):
        assert judgement not in block, f"워커가 침묵을 판정·발송하고 있다: {judgement}"


def test_delivery_marker_is_written_only_on_success() -> None:
    """발송 실패를 발송으로 적으면 침묵 감지가 영영 안 울린다."""
    alerts = (REPO_ROOT / "backend/app/notify/alerts.py").read_text(encoding="utf-8")
    fire = _code_only(alerts.split("delivered_count = await self.sender.send_to_all")[1][:800])

    # 기록은 **성공 분기 안에서만** 일어난다.
    guard = fire.split("_touch_delivery_marker")[0]
    assert "if delivered_count > 0:" in guard, "발송 성공 여부와 무관하게 기록하고 있다"


def test_marker_write_failure_does_not_block_sending() -> None:
    """기록 실패가 알림을 막으면 수리가 새 침묵을 만든다."""
    alerts = (REPO_ROOT / "backend/app/notify/alerts.py").read_text(encoding="utf-8")
    block = alerts.split("def _touch_delivery_marker")[1].split("\n    def _record")[0]
    assert "except OSError" in block


def test_silence_detector_lives_outside_the_worker() -> None:
    """3-2 항목 2 — 알림 잡 안에서 판정하면 그 잡이 죽을 때 판정도 죽는다."""
    assert "알림 침묵 감지" in DEADMAN
    assert "alert_delivery.json" in DEADMAN
    # 데드맨은 앱 코드를 import 하지 않는다 — 죽은 경로 재사용 금지.
    for app_import in ("from app.", "import app.", "uvicorn"):
        assert app_import not in DEADMAN


def test_silence_alert_bypasses_the_liveness_demotion_gate() -> None:
    """**C1 — 강등을 되돌리지 않는다.** 이것은 다른 신호이므로 다른 발송기를 쓴다."""
    block = _code_only(DEADMAN.split("send_telegram_direct()")[1].split("\nsilence_age=")[0])
    assert "DEADMAN_PUSH" not in block, "침묵 알림이 생존 알림 강등 게이트에 묶였다"
    # 생존 알림 쪽 강등은 그대로 남아 있어야 한다.
    assert 'DEADMAN_PUSH="${FCE_DEADMAN_PUSH:-0}"' in DEADMAN


def test_silence_alert_has_a_daily_cap() -> None:
    """C6 — 침묵 감지가 스팸이 되지 않게 한다."""
    assert "FCE_ALERT_SILENCE_REMIND_SECONDS:-86400" in DEADMAN
    assert "상한으로 미발송" in DEADMAN


def test_silence_is_not_declared_without_evidence() -> None:
    """발송 이력이 없는 것과 침묵은 다른 사건이다 — 모르는 것을 단정하지 않는다."""
    assert "silence_age=-1" in DEADMAN
    assert "(( silence_age >= 0 ))" in DEADMAN


def test_silence_message_states_only_facts() -> None:
    """3-2 항목 4 — 문구는 사실만."""
    assert "알림 0건 · 마지막 발송" in DEADMAN
    assert "진단 API" in DEADMAN


# ── 제약 증명 ──────────────────────────────────────────────────────────


def test_judgement_layers_are_untouched() -> None:
    """C3 — 판정·진입·출구 diff 0줄."""
    diff = subprocess.run(["git", "diff", "origin/main", "--stat", "--", *UNTOUCHABLE], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if diff.returncode != 0:
        pytest.skip("origin/main 을 참조할 수 없는 환경")
    assert diff.stdout.strip() == "", f"C3 위반:\n{diff.stdout}"


def test_no_new_alert_rule_was_added() -> None:
    """C2 — 신설은 침묵 감지 하나뿐이고 그것은 워커 밖에 있다.

    옛 형태는 `rules.py`·`delivery_gate.py` 의 **바이트 diff 0줄**이었다. 그 가드가 지키려던
    것은 "규칙이 늘거나 바뀌지 않았다"이지 "이 파일을 영원히 못 고친다"가 아니다 —
    실제로 정숙 시간 제거(2026-09-04 사용자 지시)를 이 가드가 막았다.

    그래서 **규칙 집합 자체**를 origin/main 과 대조한다. 바이트보다 좁고, 의도에는 더 정확하다:
    규칙 id 가 하나라도 늘거나 사라지거나 심각도가 바뀌면 여기서 걸린다.
    """
    from app.notify import delivery_gate, rules

    base = subprocess.run(
        ["git", "show", "origin/main:backend/app/notify/rules.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if base.returncode == 0:
        # AST 로 읽는다 — 소스를 실행하지 않으므로 임포트 부작용도 예외 주석도 없다.
        baseline = _literal_dicts(base.stdout, {"RULE_LABELS", "RULE_SEVERITY"})
        assert set(baseline["RULE_LABELS"]) == set(rules.RULE_LABELS), "알림 규칙 id 집합이 바뀌었다(C2)"
        assert baseline["RULE_SEVERITY"] == rules.RULE_SEVERITY, "알림 심각도가 바뀌었다(C2)"
    # 강등 목록도 그대로다(C1).
    assert delivery_gate.LIVENESS_DEMOTED_RULES == frozenset({"data_stall", "engine_liveness", "job_backoff_stuck", "infra_capacity", "process_restarted"})
    assert "position_opened" in rules.RULE_LABELS


def _literal_dicts(source: str, names: set[str]) -> dict[str, dict]:
    """모듈 소스에서 이름이 지정된 리터럴 dict 만 꺼낸다 (실행 없음)."""
    found: dict[str, dict] = {}
    for node in ast.parse(source).body:
        targets = node.targets if isinstance(node, ast.Assign) else ([node.target] if isinstance(node, ast.AnnAssign) else [])
        for target in targets:
            if isinstance(target, ast.Name) and target.id in names and node.value is not None:
                found[target.id] = ast.literal_eval(node.value)
    missing = names - set(found)
    assert not missing, f"origin/main 에서 {missing} 를 찾지 못했다"
    return found
