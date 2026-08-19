"""WO-FCE-ENGINE-LIVENESS-01 — 생존 감시 순수부.

원칙: **감시자는 감시 대상 안에 살 수 없다.**
- 이 모듈(내부 감시)은 "돌지만 아무것도 안 하는" 잡을 잡는다 — 프로세스가 살아있을 때만 유효.
- 프로세스 자체의 사망은 `scripts/local/deadman.sh`(외부)가 잡는다. 이 모듈은 외부 감시자가
  읽을 하트비트 스냅샷(`build_liveness_snapshot`)을 남기는 것까지만 한다.

용어:
- effective run = 조기 반환(비활성/미구성/무수확)이 아닌 실제 평가 수행. 잡 "성공"과 다르다.
  2026-07-23~27 주식 트랙 4일 정지 때 잡은 35,014회 "성공"이었다(실측 스냅샷 참조).
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.notify.rules import AlertCandidate, RULE_LABELS

# 생존 감시 대상 트랙 — 3트랙 페이퍼 + 포지션 동기화(D3: 기존엔 sync_positions 단독이었다).
# market: 해당 시장이 열려 있을 때만 stale 을 판정한다. 장 마감 시간에 "정지" 알림을 쏘면
# 매일 밤 오탐 → 사용자 뮤트 → 침묵 재발이라는 이 WO가 금지한 악순환이 된다.
TRACKED_JOBS: dict[str, dict[str, Any]] = {
    "paper_engine": {"label": "크립토 페이퍼", "market": None},  # 24/7
    "polymarket_paper": {"label": "폴리마켓 페이퍼", "market": None},
    "sync_positions": {"label": "포지션 동기화", "market": None},
}

# WO-FCE-PAPER-ENTRY-REALITY-01 (D3): 시장 단위 가상 트랙.
#
# `toss_stock_scout` **하나의 잡이 KR·US 를 함께 수집**하므로 잡 하트비트로는 시장별 정지를
# 구분할 수 없었다. 그래서 liveness 가 KR 로만 판정했고, 미국 장중(KST 22:30~05:00)에는
# market_closed 로 분류돼 **미장이 완전히 죽어도 경보가 구조적으로 불가능**했다.
#
# 해법: 잡이 아니라 **실제 평가 흔적**(시장별 분석 스냅샷 최신 시각)으로 시장별 생존을 판정한다.
# 각 시장은 자기 정규장 시간에만 평가된다.
MARKET_DATA_TRACKS: dict[str, dict[str, Any]] = {
    "stock_kr": {"label": "주식 수집(KR)", "market": "KR", "expected_interval_seconds": 900},
    "stock_us": {"label": "주식 수집(US)", "market": "US", "expected_interval_seconds": 900},
}

# 장 시작 직후엔 첫 수집까지 여유를 준다(초).
MARKET_OPEN_GRACE_SECONDS = 600


def market_session_active(market: str | None, now: datetime) -> bool:
    """해당 시장이 지금 열려 있나. market=None(코인·상시)은 항상 True."""
    if not market:
        return True
    if market == "KR":
        local = now.astimezone(ZoneInfo("Asia/Seoul"))
        open_at, close_at = (9, 0), (15, 30)
    else:  # US
        local = now.astimezone(ZoneInfo("America/New_York"))
        open_at, close_at = (9, 30), (16, 0)
    if local.weekday() >= 5:  # 주말
        return False
    start = local.replace(hour=open_at[0], minute=open_at[1], second=0, microsecond=0) + timedelta(seconds=MARKET_OPEN_GRACE_SECONDS)
    end = local.replace(hour=close_at[0], minute=close_at[1], second=0, microsecond=0)
    return start <= local <= end


# 기대 주기 대비 몇 배까지 용인할지. 기본 3배(WO 명시).
DEFAULT_STALE_MULTIPLIER = 3


def _parse(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _age_seconds(value: Any, now: datetime) -> float | None:
    parsed = _parse(value)
    return None if parsed is None else (now - parsed).total_seconds()


def _fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "기록 없음"
    if seconds < 3600:
        return f"{int(seconds // 60)}분"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}시간"
    return f"{seconds / 86400:.1f}일"


def capacity_probe(settings: Settings) -> tuple[int | None, int | None]:
    """작업 6: DB 파일 크기·디스크 여유(bytes). 측정 실패는 None(경보 미발화)."""
    try:
        from app.db.maintenance import sqlite_path

        path = sqlite_path(settings.database_url)
        if path is not None and path.exists():
            return path.stat().st_size, shutil.disk_usage(path.parent).free
    except Exception:  # 측정 실패가 워커를 멈추면 안 된다
        pass
    return None, None


def recent_restarts(settings: Settings, *, hours: int = 24) -> list[dict[str, Any]]:
    """작업 6: 외부 감시자(keepalive)가 남긴 재시작 이력 — 조용한 자동 복구를 가시화한다(C4).

    감시자가 `logs/restarts.jsonl` 에 한 줄씩 append 하고, 워커는 읽기만 한다.
    """
    path = Path(settings.worker_liveness_path).expanduser().parent / "restarts.jsonl"
    if not path.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines()[-200:]:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            stamp = _parse(row.get("at"))
            if stamp is not None and stamp >= cutoff:
                rows.append(row)
    except OSError:
        return rows
    return rows


def track_liveness(
    worker_status: dict[str, Any],
    settings: Settings,
    now: datetime | None = None,
    market_data: dict[str, str | None] | None = None,
    market_reasons: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """트랙별 생존 상태(일일 요약·진단 응답·외부 스냅샷 공용).

    stale 판정은 last_effective_run_at 기준이다. last_success_at 은 "돌지만 안 돈다"를 못 잡는다.
    market_data: 시장 단위 가상 트랙의 마지막 평가 시각(ISO) — {"stock_kr": ..., "stock_us": ...}.
    market_reasons: 시장 코드(KR/US)별 정지 사유 한 줄 — 감시가 "왜"까지 말하게 한다
    (WO-FCE-TOSS-US-STALL-01 작업 3. 사유 없는 정지 알림은 사람이 코드를 뒤지게 만든다).
    """
    now = now or datetime.now(timezone.utc)
    multiplier = max(2, int(getattr(settings, "worker_liveness_stale_multiplier", DEFAULT_STALE_MULTIPLIER)))
    jobs = worker_status.get("jobs", {}) or {}
    rows: list[dict[str, Any]] = []
    for job_name, spec in TRACKED_JOBS.items():
        label = spec["label"]
        market = spec.get("market")
        job = jobs.get(job_name) or {}
        if not job or job.get("status") == "disabled":
            rows.append(
                {
                    "job": job_name,
                    "label": label,
                    "state": "disabled" if job else "missing",
                    "effective_age_seconds": None,
                    "threshold_seconds": None,
                    "last_effective_run_at": None,
                    "stale": False,
                }
            )
            continue
        # 장 마감 중인 시장 트랙은 "정지"가 아니라 "장 종료" — 오탐 알림을 쏘지 않는다.
        if not market_session_active(market, now):
            rows.append(
                {
                    "job": job_name,
                    "label": label,
                    "state": "market_closed",
                    "effective_age_seconds": _age_seconds(job.get("last_effective_run_at"), now),
                    "threshold_seconds": None,
                    "last_effective_run_at": job.get("last_effective_run_at"),
                    "stale": False,
                }
            )
            continue
        interval = int(job.get("current_interval_seconds") or job.get("base_interval_seconds") or 0) or 300
        threshold = interval * multiplier
        effective_age = _age_seconds(job.get("last_effective_run_at"), now)
        # 한 번도 effective run 이 없으면 성공 시각으로 대체 판단하되, 그 자체가 이상 신호다.
        never_effective = effective_age is None
        age_for_judgement = effective_age if effective_age is not None else _age_seconds(job.get("last_success_at"), now)
        stale = bool(age_for_judgement is not None and age_for_judgement > threshold) or (
            never_effective and (_age_seconds(job.get("last_success_at"), now) or 0) > threshold
        )
        rows.append(
            {
                "job": job_name,
                "label": label,
                "state": "stale" if stale else "ok",
                "effective_age_seconds": effective_age,
                "threshold_seconds": threshold,
                "last_effective_run_at": job.get("last_effective_run_at"),
                "never_effective": never_effective,
                "stale": stale,
            }
        )

    # 시장 단위 가상 트랙(KR·US) — 잡 하트비트가 아니라 실제 평가 흔적으로 판정한다(D3).
    for key, spec in MARKET_DATA_TRACKS.items():
        observed_at = (market_data or {}).get(key)
        if not market_session_active(spec["market"], now):
            rows.append(
                {
                    "job": key,
                    "label": spec["label"],
                    "state": "market_closed",
                    "effective_age_seconds": _age_seconds(observed_at, now),
                    "threshold_seconds": None,
                    "last_effective_run_at": observed_at,
                    "stale": False,
                }
            )
            continue
        threshold = int(spec["expected_interval_seconds"]) * multiplier
        age = _age_seconds(observed_at, now)
        # 장중인데 관측 기록이 아예 없으면 그 자체가 정지다(미장 침묵이 여기 걸린다).
        stale = age is None or age > threshold
        rows.append(
            {
                "job": key,
                "label": spec["label"],
                "state": "stale" if stale else "ok",
                "effective_age_seconds": age,
                "threshold_seconds": threshold,
                "last_effective_run_at": observed_at,
                "never_effective": age is None,
                "stale": stale,
                "reason": (market_reasons or {}).get(str(spec["market"])),
            }
        )
    return rows


def backoff_stuck_jobs(worker_status: dict[str, Any]) -> list[dict[str, Any]]:
    """D4: base 간격보다 긴 간격에 고착된 잡 — 죽지 않았지만 사실상 멈춘 상태."""
    stuck: list[dict[str, Any]] = []
    for name, job in (worker_status.get("jobs", {}) or {}).items():
        base = int(job.get("base_interval_seconds") or 0)
        current = int(job.get("current_interval_seconds") or 0)
        if base and current > base:
            stuck.append(
                {
                    "job": name,
                    "base_interval_seconds": base,
                    "current_interval_seconds": current,
                    "multiple": round(current / base, 1),
                    "consecutive_failures": int(job.get("consecutive_failures") or 0),
                    "last_error": job.get("last_error"),
                }
            )
    return sorted(stuck, key=lambda row: -row["multiple"])


# WO-FCE-WORKER-HANG-02 Phase 2-4 — 잡 단위 기아 판정.
#
# ## D1: 감시 단위가 실패 단위보다 컸다
#
# `deadman.sh`·`supervisor.sh`·`evaluate_liveness` 는 **워커 단위**로 본다. 워커가 하트비트를
# 찍으면 정상이다. 그런데 2026-08-19 실패는 **잡 단위**에서 났다 — 하트비트는 신선한데
# `universe_scan` 이 0회 실행이었고, 어떤 신호도 나오지 않았다. 유니버스가 3종으로 말라
# 페이퍼 진입이 이틀간 0건이 됐는데도 화면은 "정상"이었다.
#
# 2026-07-28 "포트만 보는 감시" 사고와 같은 구조다. 그때는 포트가 열려 있으면 정상으로 봤다.
# **감시 단위를 실패 단위에 맞춘다.**
#
# 잡마다 자기 간격이 있으므로 절대 시간이 아니라 **자기 간격의 배수**로 잰다.
JOB_STARVATION_INTERVAL_MULTIPLE = 3.0

# 이 상태의 잡은 굶은 것이 아니다. 오탐이 쌓이면 신호 전체가 무시된다.
_NON_STARVING_STATUSES = frozenset({"disabled"})


def job_starvation(
    jobs: dict[str, Any],
    *,
    now: datetime | None = None,
    interval_multiple: float = JOB_STARVATION_INTERVAL_MULTIPLE,
) -> dict[str, Any]:
    """잡별 기아 판정 (Phase 2-4 · D1).

    두 형태를 잡는다:

    - `never_ran_and_overdue` — `runs=0` 인데 `next_run_at` 이 과거다. **2026-08-19 사고의 형태.**
      스케줄러가 발화를 계속 건너뛰면서 `next_run_at` 만 갱신하면 이 모양이 된다.
    - `interval_overrun` — 실제 마지막 유효 실행이 자기 간격의 `interval_multiple` 배를 넘겼다.

    `last_effective_run_at` 을 쓴다 — 조기 반환(비활성·미구성)은 "돌았지만 안 돈 것"이므로
    `last_success_at` 으로 재면 기아가 성공으로 보인다(`EngineLiveness.md` D3 와 같은 이유).

    **워커 생존과 별도로 돌려준다.** 워커가 살아 있어도 잡이 굶으면 보여야 한다.
    """
    now = now or datetime.now(timezone.utc)
    starved: list[str] = []
    detail: dict[str, Any] = {}
    healthy = 0

    for name, job in (jobs or {}).items():
        if not isinstance(job, dict):
            continue
        if str(job.get("status") or "") in _NON_STARVING_STATUSES:
            continue
        interval = int(job.get("base_interval_seconds") or 0)
        if interval <= 0:
            # 스케줄되지 않는 잡(간격 0)은 판정 대상이 아니다.
            continue

        runs = int(job.get("runs") or 0)
        next_run = _parse(job.get("next_run_at"))
        overdue = (now - next_run).total_seconds() if next_run is not None else None
        last_effective = _parse(job.get("last_effective_run_at"))
        age = (now - last_effective).total_seconds() if last_effective is not None else None
        limit = interval * max(1.0, interval_multiple)

        reason: str | None = None
        if runs == 0 and overdue is not None and overdue > 0:
            reason = "never_ran_and_overdue"
        elif age is not None and age > limit:
            reason = "interval_overrun"

        if reason is None:
            healthy += 1
            continue

        starved.append(name)
        detail[name] = {
            "job": name,
            "reason": reason,
            "runs": runs,
            "base_interval_seconds": interval,
            "starvation_limit_seconds": round(limit),
            "overdue_seconds": round(overdue) if overdue is not None else None,
            "effective_age_seconds": round(age) if age is not None else None,
            "misfired": int(job.get("misfired") or 0),
        }

    return {
        "starved": sorted(starved),
        "starved_count": len(starved),
        "healthy_count": healthy,
        "interval_multiple": interval_multiple,
        "detail": detail,
    }


def evaluate_liveness(
    worker_status: dict[str, Any],
    settings: Settings,
    now: datetime | None = None,
    market_data: dict[str, str | None] | None = None,
    market_reasons: dict[str, str] | None = None,
) -> list[AlertCandidate]:
    """트랙 정지·백오프 고착 알림 후보. 뮤트를 관통해 발송된다(C2) — 호출부 책임."""
    now = now or datetime.now(timezone.utc)
    candidates: list[AlertCandidate] = []

    for row in track_liveness(worker_status, settings, now, market_data, market_reasons):
        if not row["stale"]:
            continue
        age = _fmt_age(row["effective_age_seconds"])
        message = "\n".join(
            [
                f"🔴 <b>트랙 정지</b> — {row['label']}",
                f"마지막 실제 평가: {age} 전 (허용 {_fmt_age(row['threshold_seconds'])})",
                # 사유를 아는데 안 알리면 사람이 다시 코드를 뒤진다(작업 3).
                f"사유: {row['reason']}" if row.get("reason") else "잡은 '성공'으로 기록되지만 실제 평가가 없습니다 — 데이터 소스·인증을 확인하세요.",
            ]
        )
        candidates.append(
            AlertCandidate(
                rule_id="engine_liveness",
                severity="critical",
                position_id=None,
                symbol="SYSTEM",
                identity=row["job"],
                title=RULE_LABELS.get("engine_liveness", "엔진 트랙 정지"),
                message=message,
                payload={"kind": "track_stale", **row},
            )
        )

    for row in backoff_stuck_jobs(worker_status):
        message = "\n".join(
            [
                f"🟠 <b>잡 백오프 고착</b> — {row['job']}",
                f"실행 간격이 기준의 {row['multiple']}배로 늘어났습니다 (연속 실패 {row['consecutive_failures']}회)",
                f"마지막 오류: {str(row.get('last_error') or '기록 없음')[:120]}",
            ]
        )
        candidates.append(
            AlertCandidate(
                rule_id="job_backoff_stuck",
                severity="warn",
                position_id=None,
                symbol="SYSTEM",
                identity=row["job"],
                title=RULE_LABELS.get("job_backoff_stuck", "잡 백오프 고착"),
                message=message,
                payload={"kind": "backoff_stuck", **row},
            )
        )
    return candidates


def daily_liveness_lines(
    worker_status: dict[str, Any],
    settings: Settings,
    diagnosis: dict[str, Any] | None = None,
    now: datetime | None = None,
    market_data: dict[str, str | None] | None = None,
    market_reasons: dict[str, str] | None = None,
) -> list[str]:
    """작업 5: 일일 요약의 트랙별 생존 라인.

    진입이 0이어도 이 줄이 도착해야 한다 — **"살아있는데 조용한 것"과 "죽어서 조용한 것"의 구분**이
    이 WO의 목적이다. 뮤트를 관통해 발송된다(C2).
    """
    now = now or datetime.now(timezone.utc)
    gates = {}
    if isinstance(diagnosis, dict):
        for key, job in (("stock", "toss_stock_scout"), ("crypto", "paper_engine"), ("poly", "polymarket_paper")):
            track = diagnosis.get(key)
            if isinstance(track, dict) and track.get("top_reject_gate"):
                gates[job] = track["top_reject_gate"]

    lines = ["<b>트랙 생존</b>"]
    for row in track_liveness(worker_status, settings, now, market_data, market_reasons):
        if row["state"] == "missing":
            continue
        if row["state"] == "disabled":
            lines.append(f"• {row['label']}: ⚪ 비활성(플래그 꺼짐)")
            continue
        if row["state"] == "market_closed":
            lines.append(f"• {row['label']}: 🌙 장 종료 (마지막 평가 {_fmt_age(row['effective_age_seconds'])} 전)")
            continue
        mark = "🔴 정지" if row["stale"] else "🟢 정상"
        age = _fmt_age(row["effective_age_seconds"])
        extra = f" · 최다거부 {gates[row['job']]}" if gates.get(row["job"]) else ""
        # 정지 라인은 사유까지 실어야 사람이 코드를 뒤지지 않는다(작업 3).
        if row["stale"] and row.get("reason"):
            extra = f" · {row['reason']}{extra}"
        lines.append(f"• {row['label']}: {mark} · 마지막 실제 평가 {age} 전{extra}")
    return lines


def build_liveness_snapshot(
    worker_status: dict[str, Any],
    settings: Settings,
    *,
    now: datetime | None = None,
    pid: int | None = None,
    extra: dict[str, Any] | None = None,
    market_data: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """외부 데드맨 스위치가 읽을 하트비트. **워커가 살아있을 때만 갱신된다** —
    파일이 낡았다는 것 자체가 프로세스 사망의 증거다(감시자는 이 파일만 읽는다)."""
    now = now or datetime.now(timezone.utc)
    tracks = track_liveness(worker_status, settings, now, market_data)
    return {
        "schema_version": 1,
        "written_at": now.isoformat(),
        "pid": pid,
        "scheduler_running": bool(worker_status.get("scheduler_running")),
        "expected_interval_seconds": int(getattr(settings, "worker_liveness_interval_seconds", 300)),
        "tracks": [{"job": row["job"], "label": row["label"], "state": row["state"], "last_effective_run_at": row["last_effective_run_at"]} for row in tracks],
        "stale_tracks": [row["job"] for row in tracks if row["stale"]],
        "backoff_stuck": [row["job"] for row in backoff_stuck_jobs(worker_status)],
        **(extra or {}),
    }


def write_liveness_snapshot(path: str | Path, snapshot: dict[str, Any]) -> None:
    """원자적 쓰기 — 외부 감시자가 반쯤 쓰인 파일을 읽고 오탐하지 않게.

    ⚠️ `default=str` 필수(WO-FCE-ENGINE-RESTORE-01 사고). 스냅샷에는 `status()` 유래의
    datetime(예: muted_until)이 섞여 들어올 수 있고, 직렬화 실패 시 하트비트가 **조용히**
    멈춰 외부 감시자가 프로세스 사망으로 오판한다(2026-07-28: 11.7시간 오탐).
    심장박동은 어떤 이유로도 멈추면 안 되므로 표현 불가 타입은 문자열로 낮춰서라도 기록한다.
    """
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(snapshot, ensure_ascii=False, default=str), encoding="utf-8")
    temp.replace(target)


def infra_alerts(db_bytes: int | None, disk_free_bytes: int | None, settings: Settings) -> list[AlertCandidate]:
    """작업 6: DB·디스크 임계 감시. 12.8GB까지 아무도 모르던 것이 근본 문제였다."""
    candidates: list[AlertCandidate] = []
    db_limit_gb = float(getattr(settings, "db_size_alert_gb", 10.0))
    disk_min_gb = float(getattr(settings, "disk_free_alert_gb", 20.0))
    if db_bytes is not None and db_bytes / 1e9 >= db_limit_gb:
        candidates.append(
            AlertCandidate(
                rule_id="infra_capacity",
                severity="warn",
                position_id=None,
                symbol="SYSTEM",
                identity="db_size",
                title=RULE_LABELS.get("infra_capacity", "인프라 용량 경고"),
                message="\n".join(
                    [
                        "🟠 <b>DB 용량 경고</b>",
                        f"현재 {db_bytes / 1e9:.1f}GB (임계 {db_limit_gb:.0f}GB)",
                        "리텐션·incremental_vacuum 동작을 확인하세요.",
                    ]
                ),
                payload={"kind": "db_size", "bytes": db_bytes, "limit_gb": db_limit_gb},
            )
        )
    if disk_free_bytes is not None and disk_free_bytes / 1e9 <= disk_min_gb:
        candidates.append(
            AlertCandidate(
                rule_id="infra_capacity",
                severity="critical",
                position_id=None,
                symbol="SYSTEM",
                identity="disk_free",
                title=RULE_LABELS.get("infra_capacity", "인프라 용량 경고"),
                message="\n".join(
                    [
                        "🔴 <b>디스크 여유 부족</b>",
                        f"남은 공간 {disk_free_bytes / 1e9:.1f}GB (임계 {disk_min_gb:.0f}GB)",
                        "쓰기 실패로 엔진이 정지할 수 있습니다.",
                    ]
                ),
                payload={"kind": "disk_free", "bytes": disk_free_bytes, "limit_gb": disk_min_gb},
            )
        )
    return candidates


def restart_alert(restarts: list[dict[str, Any]]) -> AlertCandidate | None:
    """작업 6: keepalive 재시작 가시화 — 조용한 자동 복구 금지(C4)."""
    if not restarts:
        return None
    latest = restarts[-1]
    return AlertCandidate(
        rule_id="process_restarted",
        severity="warn",
        position_id=None,
        symbol="SYSTEM",
        identity=str(latest.get("target") or "process"),
        title=RULE_LABELS.get("process_restarted", "프로세스 재시작"),
        message="\n".join(
            [
                "🟠 <b>프로세스 재시작 감지</b>",
                f"대상: {latest.get('target')} · 시각: {str(latest.get('at'))[:19]}",
                f"최근 24시간 재시작 {len(restarts)}회 — 반복되면 원인 조사가 필요합니다.",
            ]
        ),
        payload={"kind": "keepalive_restart", "restarts": restarts[-10:], "count_24h": len(restarts)},
    )


def elapsed_excluding_gaps(
    started_at: datetime,
    effective_days: set[str],
    now: datetime | None = None,
) -> dict[str, Any]:
    """작업 6: 검증 시계 보정 — 달력일이 아니라 effective run 이 있던 날만 경과로 센다.

    유실일을 정상 경과로 계산하면 "N일 검증했다"가 거짓이 된다(C4 정직성).
    """
    now = now or datetime.now(timezone.utc)
    total_days = max(0, (now.date() - started_at.date()).days)
    counted = 0
    cursor = started_at.date()
    while cursor <= now.date():
        if cursor.isoformat() in effective_days:
            counted += 1
        cursor += timedelta(days=1)
    return {
        "calendar_days": total_days,
        "effective_days": counted,
        "lost_days": max(0, total_days - counted),
        "label": f"경과 {counted}일" + (f" (유실 {max(0, total_days - counted)}일 제외)" if total_days > counted else ""),
    }
