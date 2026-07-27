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
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.notify.rules import AlertCandidate, RULE_LABELS

# 생존 감시 대상 트랙 — 3트랙 페이퍼 + 포지션 동기화(D3: 기존엔 sync_positions 단독이었다).
TRACKED_JOBS: dict[str, str] = {
    "paper_engine": "크립토 페이퍼",
    "toss_stock_scout": "주식 수집(토스)",
    "polymarket_paper": "폴리마켓 페이퍼",
    "sync_positions": "포지션 동기화",
}

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


def track_liveness(worker_status: dict[str, Any], settings: Settings, now: datetime | None = None) -> list[dict[str, Any]]:
    """트랙별 생존 상태(일일 요약·진단 응답·외부 스냅샷 공용).

    stale 판정은 last_effective_run_at 기준이다. last_success_at 은 "돌지만 안 돈다"를 못 잡는다.
    """
    now = now or datetime.now(timezone.utc)
    multiplier = max(2, int(getattr(settings, "worker_liveness_stale_multiplier", DEFAULT_STALE_MULTIPLIER)))
    jobs = worker_status.get("jobs", {}) or {}
    rows: list[dict[str, Any]] = []
    for job_name, label in TRACKED_JOBS.items():
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


def evaluate_liveness(worker_status: dict[str, Any], settings: Settings, now: datetime | None = None) -> list[AlertCandidate]:
    """트랙 정지·백오프 고착 알림 후보. 뮤트를 관통해 발송된다(C2) — 호출부 책임."""
    now = now or datetime.now(timezone.utc)
    candidates: list[AlertCandidate] = []

    for row in track_liveness(worker_status, settings, now):
        if not row["stale"]:
            continue
        age = _fmt_age(row["effective_age_seconds"])
        message = "\n".join(
            [
                f"🔴 <b>트랙 정지</b> — {row['label']}",
                f"마지막 실제 평가: {age} 전 (허용 {_fmt_age(row['threshold_seconds'])})",
                "잡은 '성공'으로 기록되지만 실제 평가가 없습니다 — 데이터 소스·인증을 확인하세요.",
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
    for row in track_liveness(worker_status, settings, now):
        if row["state"] == "missing":
            continue
        if row["state"] == "disabled":
            lines.append(f"• {row['label']}: ⚪ 비활성(플래그 꺼짐)")
            continue
        mark = "🔴 정지" if row["stale"] else "🟢 정상"
        age = _fmt_age(row["effective_age_seconds"])
        extra = f" · 최다거부 {gates[row['job']]}" if gates.get(row["job"]) else ""
        lines.append(f"• {row['label']}: {mark} · 마지막 실제 평가 {age} 전{extra}")
    return lines


def build_liveness_snapshot(
    worker_status: dict[str, Any],
    settings: Settings,
    *,
    now: datetime | None = None,
    pid: int | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """외부 데드맨 스위치가 읽을 하트비트. **워커가 살아있을 때만 갱신된다** —
    파일이 낡았다는 것 자체가 프로세스 사망의 증거다(감시자는 이 파일만 읽는다)."""
    now = now or datetime.now(timezone.utc)
    tracks = track_liveness(worker_status, settings, now)
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
    """원자적 쓰기 — 외부 감시자가 반쯤 쓰인 파일을 읽고 오탐하지 않게."""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
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
