"""WO-FCE-LIVENESS-VERDICT-01 — 생존 판정 정합 회귀.

배경 (실측 2026-08-13 KST):

```
15:21  사망 · 1063초 · 8875 응답 없음 · 최근 재시작 27회
15:22  복구 · 경과 4초
16:20  자동 복구 포기 · 978초 · 최근 1시간 재시작 3회
16:20  사망 · 1010초 · 8875 리스닝 중 · 최근 재시작 27회
17:04  복구 · 경과 709초          ← 12분 묵은 하트비트가 "복구"
```

**사망과 복구가 같은 문턱 900초를 썼다.** 히스테리시스가 없어 문턱 근처에서 진동했고,
709초 묵은 하트비트가 "다시 갱신되고 있습니다"로 나갔다.

고정하는 명제:
1. 복구 문턱은 사망 문턱과 **다르고 더 낮다** — 709초는 복구가 아니다 (A1)
2. 복구는 **지속**을 요구한다 — 단발 갱신 하나로 선언되지 않는다 (A1)
3. 포기 래치도 같은 기준으로 풀린다 — 단발로 풀리지 않는다 (A2)
4. 재시작 카운터가 **매달림 재시작을 센다** (A3)
5. 알림 카운터와 포기 판정이 **같은 원장·같은 창** (A4)
6. 포기 메시지가 상태를 갱신해 직후 중복 사망 알림을 막는다 (A5)
7. 복구 알림 발송 실패 시 상태가 `dead` 로 유지돼 재시도된다 (A6)
8. 사망 문턱은 **올라가지 않는다** (C1)
9. `backend/app/` diff 0줄 (C7)
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts" / "local"
DEADMAN = SCRIPTS / "deadman.sh"
SUPERVISOR = SCRIPTS / "supervisor.sh"
LIB = SCRIPTS / "lib" / "liveness.sh"


@pytest.fixture()
def host(tmp_path: Path) -> Path:
    """감시 스크립트가 기대하는 트리를 합성한다 (`$REPO_DIR/{scripts/local,logs,backend}`)."""
    local = tmp_path / "scripts" / "local"
    (local / "lib").mkdir(parents=True)
    for source in (DEADMAN, SUPERVISOR):
        shutil.copy2(source, local / source.name)
    shutil.copy2(LIB, local / "lib" / "liveness.sh")
    (tmp_path / "logs").mkdir()
    (tmp_path / "backend").mkdir()
    # 텔레그램 자격증명을 두지 않는다 → send_telegram 이 실패하고 **보내려던 문구를 로그에 남긴다.**
    # 네트워크 없이 "무엇을 발송하려 했는가"를 관측할 수 있는 지점이다.
    (tmp_path / "backend" / ".env").write_text("FCE_DUMMY=1\n", encoding="utf-8")
    return tmp_path


def _heartbeat(host: Path, *, age_seconds: int) -> None:
    written = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    (host / "logs" / "liveness.json").write_text(json.dumps({"written_at": written.isoformat()}), encoding="utf-8")


def _state(host: Path, raw: str) -> None:
    (host / "logs" / "deadman.state").write_text(raw + "\n", encoding="utf-8")


def _read_state(host: Path) -> list[str]:
    return (host / "logs" / "deadman.state").read_text(encoding="utf-8").strip().split("|")


def _ledger(host: Path, entries: list[tuple[int, str, str]]) -> None:
    """(분 전, target, reason) 목록을 restarts.jsonl 로."""
    lines = []
    for minutes_ago, target, reason in entries:
        at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        lines.append(json.dumps({"at": at.isoformat(), "target": target, "reason": reason}))
    (host / "logs" / "restarts.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_deadman(host: Path, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(host / "scripts" / "local" / "deadman.sh")],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(host), **env},
    )


def _deadman_log(host: Path) -> str:
    path = host / "logs" / "deadman.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _lib(host: Path, snippet: str) -> str:
    """공용 규약 함수를 직접 호출한다."""
    script = f'. "{host}/scripts/local/lib/liveness.sh"\n{textwrap.dedent(snippet)}'
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


# ── 1. 복구 문턱 분리 (A1) ──────────────────────────────────────────────


def test_death_and_recovery_no_longer_share_one_threshold(host: Path) -> None:
    """사망 900 · 복구 300. 같은 문턱이면 그 근처에서 영원히 진동한다."""
    stale = _lib(host, 'echo "$FCE_STALE_LIMIT"')
    fresh = _lib(host, 'echo "$FCE_FRESH_LIMIT"')

    assert stale == "900"
    assert int(fresh) < int(stale), "복구 문턱이 사망 문턱보다 낮아야 히스테리시스가 성립한다"


def test_the_709_second_case_is_not_fresh(host: Path) -> None:
    """실측 17:04 — 709초는 900 아래지만 복구가 아니다."""
    assert _lib(host, "liveness_is_fresh 709 && echo yes || echo no") == "no"
    assert _lib(host, "liveness_is_fresh 4 && echo yes || echo no") == "yes", "15:22 의 4초는 진짜 신선이다"
    assert _lib(host, "liveness_is_stale 709 && echo yes || echo no") == "no", "709 는 사망도 아니다 — 그래서 중간 상태가 필요하다"


def test_recovery_is_not_declared_for_a_stale_but_sub_threshold_heartbeat(host: Path) -> None:
    """709초 하트비트에 "복구" 알림이 나가면 안 된다 — 이 WO 의 핵심 회귀."""
    _heartbeat(host, age_seconds=709)
    _state(host, "dead|0|0|0")

    _run_deadman(host)

    log = _deadman_log(host)
    assert "엔진 복구" not in log, "709초를 복구로 선언했다"
    assert "복구 미달" in log, "중간 상태가 로그에 남아야 한다(C5)"
    assert _read_state(host)[0] == "dead"


def test_a_single_fresh_tick_does_not_declare_recovery(host: Path) -> None:
    """단발 갱신 하나로는 복구가 아니다 — 지속을 요구한다."""
    _heartbeat(host, age_seconds=10)
    _state(host, "dead|0|0|0")

    _run_deadman(host)

    log = _deadman_log(host)
    assert "엔진 복구" not in log
    assert "복구 대기" in log
    status, _notified, _selfcheck, fresh_since = _read_state(host)
    assert status == "dead"
    assert int(fresh_since) > 0, "신선 구간 진입 시각이 기록돼야 지속을 셀 수 있다"


def test_recovery_is_declared_after_sustained_freshness(host: Path) -> None:
    """지속 조건을 만족하면 복구다. 15:22 의 4초 복구는 이 경로로 살아 있어야 한다."""
    _heartbeat(host, age_seconds=10)
    sustained_since = int((datetime.now(timezone.utc) - timedelta(seconds=600)).timestamp())
    _state(host, f"dead|0|0|{sustained_since}")

    _run_deadman(host)

    assert "엔진 복구" in _deadman_log(host)


def test_recovery_streak_resets_when_freshness_lapses(host: Path) -> None:
    """신선이 끊기면 지속 시계도 0으로 — 진동이 누적으로 복구를 사지 못하게 한다."""
    _heartbeat(host, age_seconds=709)
    sustained_since = int((datetime.now(timezone.utc) - timedelta(seconds=600)).timestamp())
    _state(host, f"dead|0|0|{sustained_since}")

    _run_deadman(host)

    assert "엔진 복구" not in _deadman_log(host)
    assert _read_state(host)[3] == "0"


def test_legacy_three_field_state_file_still_loads(host: Path) -> None:
    """구 상태 파일(3필드)이 있어도 기동해야 한다 — 배포 시점에 파일이 이미 존재한다."""
    _heartbeat(host, age_seconds=10)
    _state(host, "dead|0|0")

    result = _run_deadman(host)

    assert result.returncode == 0
    assert len(_read_state(host)) == 4


# ── 2. 포기 래치 (A2) ───────────────────────────────────────────────────


def test_giveup_latch_release_requires_sustained_freshness(host: Path) -> None:
    """`age <= 900` 한 틱으로 래치가 풀리면 16:52 단발이 16:20 포기를 지운다."""
    now = int(datetime.now(timezone.utc).timestamp())

    # 709초: 사망 문턱 이내지만 신선 아님 → 지속 시계가 시작조차 안 된다
    assert _lib(host, "liveness_is_fresh 709 && echo yes || echo no") == "no"
    # 신선해도 지속 전에는 해제 불가
    assert _lib(host, f"liveness_recovery_confirmed {now - 10} {now} && echo yes || echo no") == "no"
    # 지속 후에만 해제
    assert _lib(host, f"liveness_recovery_confirmed {now - 600} {now} && echo yes || echo no") == "yes"


def test_supervisor_latch_uses_the_shared_criterion() -> None:
    """두 감시자가 다른 기준을 쓰면 그중 하나는 반드시 잊힌다."""
    source = SUPERVISOR.read_text(encoding="utf-8")

    assert "liveness_is_fresh" in source
    assert "liveness_recovery_confirmed" in source
    assert "HB_GIVEUP_NOTIFIED=0" in source
    # 단발 해제 경로가 남아 있으면 안 된다.
    assert '[ "$age" -le "$HB_STALE_LIMIT" ] 2>/dev/null && { HB_GIVEUP_NOTIFIED=0; return 0; }' not in source


# ── 3. 재시작 카운터 (A3·A4·A8) ─────────────────────────────────────────


def test_counter_now_sees_heartbeat_stale_restarts(host: Path) -> None:
    """A3 — 예전 `grep -c "down → restart"` 는 매달림 재시작을 한 건도 세지 않았다."""
    _ledger(host, [(5, "backend:8875:heartbeat_stale", "heartbeat_stale"), (10, "backend:8875", "port_down")])

    counts = _lib(host, f'liveness_restart_counts "{host}/logs/restarts.jsonl"').split()

    total, heartbeat_stale, port_down, backend, frontend = (int(value) for value in counts)
    assert total == 2
    assert heartbeat_stale == 1, "매달림 재시작이 카운터에 보여야 한다 — 이것이 A3 의 수리다"
    assert port_down == 1
    assert backend == 2 and frontend == 0


def test_counter_separates_backend_from_frontend(host: Path) -> None:
    """예전 grep 은 frontend 재시작도 같은 숫자에 뭉갰다."""
    _ledger(host, [(5, "backend:8875", "port_down"), (6, "frontend:8876", "port_down")])

    counts = _lib(host, f'liveness_restart_counts "{host}/logs/restarts.jsonl"').split()

    assert counts[3] == "1" and counts[4] == "1"


def test_counter_applies_the_declared_window(host: Path) -> None:
    """변수명 `restarts_24h` 에 24시간 필터가 없었다 — 파일 전체를 셌다(A4)."""
    _ledger(host, [(30, "backend:8875", "port_down"), (60 * 40, "backend:8875", "port_down")])

    within_24h = _lib(host, f'liveness_restart_counts "{host}/logs/restarts.jsonl" 24').split()[0]
    within_1h = _lib(host, f'liveness_restart_counts "{host}/logs/restarts.jsonl" 1').split()[0]

    assert within_24h == "1", "40시간 전 건은 24h 창 밖이다"
    assert within_1h == "1"


def test_alert_line_states_what_it_counts(host: Path) -> None:
    """C8 — 숫자만 있으면 다음 사람이 또 다른 의미로 읽는다."""
    _ledger(host, [(5, "backend:8875:heartbeat_stale", "heartbeat_stale")])

    line = _lib(host, f'liveness_restart_line "{host}/logs/restarts.jsonl"')

    assert "24h" in line and "매달림" in line and "포트다운" in line


def test_empty_ledger_does_not_print_double_zero(host: Path) -> None:
    """A8 — `grep -c` 는 0건에도 `0` 을 출력하고 exit 1 이라 `|| echo 0` 이 겹쳐 "0 0회"가 됐다."""
    _heartbeat(host, age_seconds=2000)
    _state(host, "ok|0|0|0")

    _run_deadman(host)

    log = _deadman_log(host)
    assert "0\n0" not in log
    assert "재시작 0회" in log


def test_giveup_and_alert_read_the_same_ledger() -> None:
    """A4 — 예전에는 알림이 supervisor.log grep, 포기 판정이 restarts.jsonl 이었다."""
    supervisor = SUPERVISOR.read_text(encoding="utf-8")
    deadman = DEADMAN.read_text(encoding="utf-8")

    assert "liveness_restarts_in_window" in supervisor
    assert "liveness_restart_line" in deadman
    for source in (supervisor, deadman):
        assert 'grep -c "down → restart"' not in source, "로그 문자열 파싱은 문자열이 바뀌면 조용히 깨진다"


# ── 4. 알림 경로 정합 (A5·A6) ───────────────────────────────────────────


def test_giveup_message_updates_the_state(host: Path) -> None:
    """A5 — 예전 FORCE_MESSAGE 경로는 상태를 읽지도 쓰지도 않고 빠져나갔다."""
    _heartbeat(host, age_seconds=1010)
    _state(host, "ok|0|0|0")

    # 이 테스트가 지키는 것은 "발송 실패가 상태를 전진시키지 않는다"이므로 발송 경로를
    # 명시적으로 켠다. 기본값은 강등(미발송)이며 그것은 별도 테스트가 고정한다.
    _run_deadman(host, FCE_DEADMAN_FORCE_MESSAGE="🛑 자동 복구 포기 테스트", FCE_DEADMAN_PUSH="1")

    log = _deadman_log(host)
    assert "자동 복구 포기 테스트" in log
    # 자격증명이 없어 발송이 실패했으므로 상태는 유지되고 재시도 대상으로 남는다.
    assert "상태 유지(재시도 대상)" in log


def test_recovery_send_failure_keeps_the_dead_state(host: Path) -> None:
    """A6 — 예전 복구 경로는 발송 성공 여부를 보지 않고 상태를 ok 로 넘겼다.

    curl 이 죽으면 복구 알림이 **영영 오지 않는다.** 사망 경로는 이 실패를 처리하고 있었다.
    """
    _heartbeat(host, age_seconds=10)
    sustained_since = int((datetime.now(timezone.utc) - timedelta(seconds=600)).timestamp())
    _state(host, f"dead|0|0|{sustained_since}")

    # 발송 실패 처리를 검증하는 테스트이므로 발송 경로를 켠다(기본값은 강등·미발송).
    _run_deadman(host, FCE_DEADMAN_PUSH="1")

    assert "복구 알림 발송 실패" in _deadman_log(host)
    assert _read_state(host)[0] == "dead", "발송 실패인데 ok 로 넘기면 복구 알림이 소실된다"


def test_death_alert_still_fires_and_reports_process_state(host: Path) -> None:
    """강등이 아니라 정합이다 — 사망 알림 자체는 그대로 나가야 한다."""
    _heartbeat(host, age_seconds=1063)
    _state(host, "ok|0|0|0")

    _run_deadman(host)

    log = _deadman_log(host)
    assert "엔진 사망 감지" in log
    assert "8875" in log


# ── 5. 이중 기동 방지 (A7) ──────────────────────────────────────────────


def test_restart_lock_guards_the_port_watch_with_a_ttl() -> None:
    """표식이 남아 영구히 재시작을 막으면 그게 새로운 침묵이다(C5)."""
    source = SUPERVISOR.read_text(encoding="utf-8")

    assert "take_restart_lock" in source
    assert "restart_lock_held" in source
    assert "RESTART_LOCK_TTL" in source
    assert "TTL 만료" in source, "만료 사유가 로그로 남아야 한다"
    assert "if ! restart_lock_held; then" in source


def test_restart_lock_expires(host: Path) -> None:
    lock = host / "logs" / "restart.lock"
    lock.write_text(str(int(datetime.now(timezone.utc).timestamp()) - 5000), encoding="utf-8")
    script = f"""
    LOG_DIR="{host}/logs"
    RESTART_LOCK="$LOG_DIR/restart.lock"
    RESTART_LOCK_TTL=120
    log() {{ echo "$*"; }}
    {_restart_lock_held_source()}
    restart_lock_held && echo held || echo expired
    """
    result = subprocess.run(["bash", "-c", textwrap.dedent(script)], capture_output=True, text=True, check=False)

    assert "expired" in result.stdout
    assert not lock.exists()


def _restart_lock_held_source() -> str:
    source = SUPERVISOR.read_text(encoding="utf-8")
    start = source.index("restart_lock_held() {")
    end = source.index("\n}", start) + 2
    return source[start:end]


# ── 6. 경계 (C1·C2·C3·C7) ───────────────────────────────────────────────


def test_stale_thresholds_were_not_relaxed() -> None:
    """C1 — 900을 늘리면 사망 판정이 줄 뿐 매달림은 그대로다. 그건 수리가 아니라 은폐다."""
    assert "FCE_DEADMAN_STALE_SECONDS:-900" in LIB.read_text(encoding="utf-8")
    assert "FCE_SUPERVISOR_HB_STALE_SECONDS:-$FCE_STALE_LIMIT" in SUPERVISOR.read_text(encoding="utf-8")


def test_cooldown_and_hourly_cap_are_intact() -> None:
    """C6 — A2 는 래치를 더 오래 유지하는 수리다. 폭주 방지를 약화시키지 않는다."""
    source = SUPERVISOR.read_text(encoding="utf-8")

    assert "FCE_SUPERVISOR_HB_COOLDOWN_SECONDS:-600" in source
    assert "FCE_SUPERVISOR_HB_MAX_RESTARTS_PER_HOUR:-3" in source


# 창·판정 모듈. 하네스(`*_replay.py` · `history_backfill.py` · `published_values.py`)는
# 판정을 **호출만** 하므로 여기에 없다.
def _offending_verdict_changes(diff: str) -> list[str]:
    """판정 로직 변경만 골라낸다 — **트랙 등록은 판정이 아니다.**

    3차 축소(WO-FCE-WHALE-FOLLOW-01 Phase 6-2): `sample_viability.py` 는 판정 로직과
    **트랙 레지스트리**(`TRACK_SAMPLE_SPECS`)를 한 파일에 갖고 있다. 새 트랙을 등록하는
    것은 "무엇을 세는가"를 데이터로 선언하는 행위이지 "어떻게 판정하는가"를 바꾸는 것이
    아니다. 파일 단위 고정은 그 둘을 구분하지 못해, 트랙 추가가 영원히 막힌다.

    그래서 **삭제를 허용하지 않고**, 추가는 `SampleSpec` 항목 안일 때만 허용한다.
    판정 로직 한 줄이라도 바뀌면 여기서 걸린다 — 가드의 이빨은 그대로다.
    """
    allowed_added = ("SampleSpec(", "key=", "label=", "entry_sql=", "scored_sql=", "scoring_definition=", "),", '"whale_follow"', '"poly"')
    # 트랙 스펙 **필드**는 고칠 수 있다 — 단 조건이 붙는다.
    #
    # 4차 축소(WO-FCE-DEFAULTS-01 1-5): `poly` 의 `scored_sql` 이 분자에 시장 전체 관측을,
    # 분모에 우리 포지션을 두고 있었다. 청산 완료율이 141,933% 로 나오고 표본 미달이
    # "충분"으로 표기됐다 — 버그이고 임계 변경이 아니다.
    #
    # 그런데 스펙 삭제를 무조건 허용하면 임계를 스펙에 숨겨 낮출 수 있다. 그래서
    # **불변 증명 테스트의 존재를 조건으로** 묶는다 — 스펙을 고치려면 수정 전후 판정이
    # 같다는 테스트가 함께 있어야 한다. 그 테스트가 없으면 여기서 걸린다.
    spec_fields = ("entry_sql=", "scored_sql=", "scoring_definition=", "key=", "label=")
    invariance_test_exists = (
        "def test_track_spec_change_keeps_every_verdict" in (Path(__file__).parent / "test_sample_viability_spec.py").read_text(encoding="utf-8")
        if (Path(__file__).parent / "test_sample_viability_spec.py").exists()
        else False
    )
    offending: list[str] = []
    for line in diff.splitlines():
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("-") and line[1:].strip():
            body = line[1:].strip()
            if any(field in body for field in spec_fields) and invariance_test_exists:
                # 스펙 필드 수정 + 불변 증명 테스트 존재 → 허용.
                continue
            offending.append(f"삭제됨: {line}")
            continue
        if not line.startswith("+"):
            continue
        body = line[1:].strip()
        if not body:
            continue
        # 주석은 로직이 아니다. 판정을 바꿀 수 없으므로 허용한다 — 오히려 스펙을 왜 고쳤는지
        # 남기는 것이 이 가드가 원하는 것이다. 코드 줄만 검사한다.
        if body.startswith("#") or body.startswith('"""') or body.startswith('"'):
            continue
        if not any(token in body for token in allowed_added):
            offending.append(f"추가됨(트랙 등록 아님): {line}")
    return offending


VERDICT_MODULES = (
    "backend/app/validation/verdict_watch.py",
    "backend/app/validation/window_anchor.py",
    "backend/app/validation/live_trading_gate.py",
    "backend/app/validation/sample_rate.py",
    "backend/app/validation/sample_viability.py",
    "backend/app/validation/decay.py",
    "backend/app/validation/engine.py",
    "backend/app/validation/candidates.py",
)

# `pending_decisions.py` 는 여기서 뺐다 (WO-FCE-STOCK-STATUS-01 3-5).
#
# 이 목록의 나머지는 **검증 판정을 계산**한다 — 통과·미달·유효일·감쇠를 정한다.
# `pending_decisions` 는 아무것도 판정하지 않는다. 사람이 결정할 항목을 나열할 뿐이고,
# 그 항목의 등급은 실측이 바뀌면 함께 바뀌어야 한다(유실 19일 → 검증 불가 → 차단 등급).
# 파일을 얼리면 그 갱신이 영원히 막히는데, 그것은 이 가드가 지키려던 것이 아니다.
#
# 대신 이 모듈에서 실제로 위험한 것 — **등급이 조용히 낮아지는 것** — 을
# `test_stock_track_status.py` 가 속성으로 고정한다. 완화는 사람만 할 수 있다는 규칙은
# 파일 동결이 아니라 그 속성으로 지킨다.


def test_watcher_stays_outside_the_worker() -> None:
    """C2·C3 — 감시자가 감시 대상 안으로 들어가면 침묵이 스스로를 은폐한다."""
    for source in (DEADMAN.read_text(encoding="utf-8"), LIB.read_text(encoding="utf-8")):
        assert "from app." not in source and "import app" not in source


def test_verdict_layer_is_untouched_by_watcher_work() -> None:
    """C7 — 감시 계층 작업이 **검증 창·판정 로직**을 건드리지 않는다.

    처음에는 `backend/app/` 전체를 origin/main 과 동일하게 고정했으나, 그것은 이 WO 한정의
    일회성 범위 확인이지 영구 불변이 아니었다. 그대로 두면 이후 모든 백엔드 작업이
    막힌다(WO-FCE-WORKER-HANG-02 의 루프 지연 계측이 실제로 여기에 걸렸다).

    영구히 지켜야 하는 것은 "감시 계층을 고치면서 판정을 함께 바꾸지 않는다"이므로
    대상을 판정 모듈로 좁힌다. 감시자가 워커 밖에 있다는 계약은
    `test_watcher_stays_outside_the_worker` 가 따로 고정한다.

    2차 축소(WO-FCE-REPLAY-DEPTH-01 4-4): `app/validation/` **패키지 전체**를 고정하면 같은
    문제가 한 칸 안에서 재발한다 — 그 패키지는 판정 모듈만이 아니라 **재판정 하네스**
    (`directional_replay` · `risk_sizing_replay` · `history_backfill` · `paper_replay`)의 집이고,
    하네스는 계속 늘어난다. 판정을 바꾸지 않으면서 하네스를 추가하는 작업이 막히면 안 된다.

    그래서 대상을 **창·판정 모듈 파일**로 명시한다. 새 판정 모듈이 생기면 여기 추가한다 —
    목록에 적는 행위가 곧 검토 지점이다.
    """
    diff = subprocess.run(
        ["git", "diff", "origin/main", "--", *VERDICT_MODULES],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if diff.returncode != 0:
        pytest.skip("origin/main 을 참조할 수 없는 환경")

    offending = _offending_verdict_changes(diff.stdout)
    assert offending == [], "검증 판정 계층이 변경됐다:\n" + "\n".join(offending)


def test_no_new_push_alert_was_added() -> None:
    """Phase 1~4 는 로그로 관측한다. 새 푸시를 만들지 않는다 (고래 스팸 선례)."""
    source = DEADMAN.read_text(encoding="utf-8")
    call_sites = [line for line in source.splitlines() if "send_telegram " in line and "send_telegram()" not in line]

    # 강제 메시지 · 사망 · 복구 · 자가 점검 = 4개. 신설 없음.
    assert len(call_sites) == 4, f"발송 지점이 늘었다: {call_sites}"
    assert "복구 대기" in source and "복구 미달" in source, "중간 상태는 알림이 아니라 로그다"


# ── 7. Phase 0 도구 · 문서 ──────────────────────────────────────────────


def test_evidence_collector_is_read_only() -> None:
    """Phase 0 은 구현이 아니라 수집이다. 무엇도 고치지 않는다."""
    source = (SCRIPTS / "collect-liveness-evidence.sh").read_text(encoding="utf-8")
    # 주석·설명 문구는 상태를 바꾸지 않는다. 실행되는 줄만 본다.
    executable = "\n".join(line for line in source.splitlines() if line.strip() and not line.strip().startswith("#"))

    for forbidden in ("kill ", "rm ", "mv ", '> "$LOG_DIR', '>> "$LOG_DIR'):
        assert forbidden not in executable, f"수집기가 상태를 바꾼다: {forbidden}"
    # 감시 스크립트를 source 하면 그쪽 부작용을 그대로 물려받는다 — 수집기는 독립이어야 한다.
    assert '. "$(dirname' not in executable and 'supervisor.sh"' not in executable
    for item in ("H1", "H2", "restarts.jsonl", "pgrep"):
        assert item in source


def test_runbook_documents_both_thresholds() -> None:
    text = (REPO_ROOT / "docs" / "RESTART_RUNBOOK.md").read_text(encoding="utf-8")

    for item in ("복구 문턱", "사망 문턱", "지속", "포기 래치"):
        assert item in text, f"런북에 '{item}' 이 없다"


def test_host_persistence_records_that_hangs_are_not_sleep() -> None:
    """03:21·04:20·15:21·16:20 은 절전 구간이 아니다 — 절전 대책만으로 해결되지 않는다."""
    text = (REPO_ROOT / "docs" / "validation" / "HOST_PERSISTENCE.md").read_text(encoding="utf-8")

    assert "절전 구간이 아니" in text
    assert "15:21" in text


def test_deadman_does_not_push_by_default(host: Path) -> None:
    """사용자 지시(2026-08-16) — 생존·사망·복구는 푸시하지 않는다.

    감시·판정·기록은 그대로 돌고 **발송만** 로그로 돌린다. 이 테스트가 깨지면
    "하트비트 죽는거 그만 보고해"가 되돌려진 것이다.
    """
    _heartbeat(host, age_seconds=1010)
    _state(host, "ok|0|0|0")

    _run_deadman(host, FCE_DEADMAN_FORCE_MESSAGE="🛑 이 메시지는 발송되면 안 된다")

    log = _deadman_log(host)
    assert "생존 알림 미발송(푸시 강등)" in log
    assert "이 메시지는 발송되면 안 된다" in log, "미발송이어도 내용은 기록돼야 한다(조회 가능)"
