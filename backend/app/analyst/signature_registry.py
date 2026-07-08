"""시그니처 상태 머신 (WO-FCE-37 §1).

상태: candidate → validated → degraded → quarantined

비대칭 자율 (Asymmetric Autonomy):
- 강등·격리·보수화 = 자율 즉시 (통보만)
- 승격·완화·복귀 = 제안-승인 필수 (WO-22 불변)

근거 스냅샷 없는 상태 전이는 금지한다.
"""

from __future__ import annotations

from typing import Any

from app.db.models import AutonomyLog, SignatureState

SIGNATURE_STATE_LABELS: dict[str, str] = {
    "candidate": "후보 (표본 부족)",
    "validated": "검증됨",
    "degraded": "성능 저하 관찰 중",
    "quarantined": "격리됨",
}

# 발견 게이트에서 제외되는 상태 (자율 강등의 실효).
GATE_EXCLUDED_STATES = {"degraded", "quarantined"}
# 브리핑 증거에서 제외되는 상태.
EVIDENCE_SUPPRESSED_STATES = {"quarantined"}
# 복귀(승급) 방향 — 자율 실행 금지, 제안-승인 경유만.
UPGRADE_STATES = {"validated"}
DOWNGRADE_ORDER = ["candidate", "validated", "degraded", "quarantined"]


def base_state(stat: dict[str, Any] | None, *, min_sample: int = 30, min_ci_low: float = 50.0) -> SignatureState:
    """로그가 없는 신규 시그니처의 기본 상태 — WO-36 게이트 기준(N·CI 하한)."""
    if not isinstance(stat, dict):
        return "candidate"
    n = int(stat.get("sample_size") or 0)
    ci_low = _ci_low(stat)
    if n >= min_sample and ci_low is not None and ci_low >= min_ci_low:
        return "validated"
    return "candidate"


def current_state(
    repo: Any,
    signature_key: str,
    *,
    stat: dict[str, Any] | None = None,
    settings: Any = None,
) -> SignatureState:
    """시그니처의 현재 상태 — 최신 전이 로그가 있으면 그것, 없으면 기본 판정."""
    logs = repo.list_autonomy_logs(signature_key=signature_key, limit=1)
    if logs:
        return logs[0].new_state
    return base_state(stat, min_sample=_min_sample(settings), min_ci_low=_min_ci(settings))


def state_map(repo: Any, *, limit: int = 2000) -> dict[str, SignatureState]:
    """전이 로그가 있는 시그니처의 최신 상태 맵.

    `latest_autonomy_states`(그룹 최신 행)를 우선 사용 — 로그 총량이 limit를 넘어도
    오래된 시그니처의 상태가 잘려나가 격리가 조용히 풀리는 일을 방지한다.
    """
    latest = getattr(repo, "latest_autonomy_states", None)
    if callable(latest):
        return {key: state for key, state in latest().items()}  # type: ignore[return-value]
    result: dict[str, SignatureState] = {}
    for log in repo.list_autonomy_logs(limit=limit):  # 최신순
        result.setdefault(log.signature_key, log.new_state)
    return result


def record_transition(
    repo: Any,
    *,
    signature_key: str,
    previous: SignatureState | None,
    new: SignatureState,
    transition: str,
    reason: str,
    evidence: dict[str, Any],
    autonomous: bool,
    regime: str | None = None,
) -> AutonomyLog:
    """전이를 원장에 기록. 근거 스냅샷 필수, 자율 복귀(validated 상향) 금지."""
    if not evidence:
        raise ValueError("state transition requires an evidence snapshot (WO-37)")
    if autonomous and new in UPGRADE_STATES and previous in {"degraded", "quarantined"}:
        raise ValueError("recovery to validated must be proposal-gated, not autonomous (asymmetric autonomy)")
    log = AutonomyLog(
        signature_key=signature_key,
        previous_state=previous,
        new_state=new,
        transition=transition,
        reason=reason,
        autonomous=autonomous,
        regime=regime,
        evidence=evidence,
    )
    return repo.add_autonomy_log(log)


def gate_excluded(state: str | None) -> bool:
    return state in GATE_EXCLUDED_STATES


def evidence_suppressed(state: str | None) -> bool:
    return state in EVIDENCE_SUPPRESSED_STATES


def state_note(state: str | None) -> str | None:
    if state == "degraded":
        return "성능 저하 관찰 중 — 가중 하향"
    if state == "quarantined":
        return "격리됨 — 증거 제외 (복귀 판정용 데이터는 계속 수집)"
    return None


def _ci_low(stat: dict[str, Any]) -> float | None:
    ci = stat.get("win_1r_ci")
    if not isinstance(ci, (list, tuple)) or len(ci) != 2:
        inner = stat.get("payload") if isinstance(stat.get("payload"), dict) else {}
        ci = inner.get("win_1r_ci")
    if isinstance(ci, (list, tuple)) and len(ci) == 2:
        try:
            return float(ci[0])
        except (TypeError, ValueError):
            return None
    return None


def _min_sample(settings: Any) -> int:
    return int(getattr(settings, "signature_validated_min_sample", 30)) if settings else 30


def _min_ci(settings: Any) -> float:
    return float(getattr(settings, "universe_backtest_min_ci_low_pct", 50.0)) if settings else 50.0
