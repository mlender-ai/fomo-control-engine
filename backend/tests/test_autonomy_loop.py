"""WO-FCE-37 자율 검증 루프 — 수용 기준 테스트.

비대칭 자율(강등/격리 자율, 복귀 제안), autonomy_log 스냅샷·오판율, 주간 상한·킬스위치.
"""

from __future__ import annotations

import pytest

from app.analyst.signature_registry import current_state, record_transition
from app.core.config import Settings
from app.db.models import BacktestStat
from app.db.repository import MemoryRepository
from app.scout.universe import evaluate_discovery_gate
from app.validation.decay import (
    apply_recovery,
    autonomy_scorecard,
    build_self_audit,
    evaluate_signature,
    run_decay_sweep,
)


def _settings(**overrides) -> Settings:
    base = {"autonomy_enabled": True, "autonomy_weekly_transition_cap": 5}
    base.update(overrides)
    return Settings(**base)


def _windows(specs: list[tuple[float, int]]) -> list[dict]:
    return [
        {
            "window_start": f"2025-0{i + 1}-01T00:00:00+00:00",
            "window_end": f"2025-0{i + 2}-01T00:00:00+00:00",
            "sample_size": n,
            "win_1r_pct": win,
            "sample_sufficient": n >= 5,
        }
        for i, (win, n) in enumerate(specs)
    ]


def _stat(key: str, *, win: float, ci: list[float], unstable: bool, windows: list[dict], regimes: dict | None = None) -> BacktestStat:
    engine, event, strength, direction, asset, tf = key.split(":")
    return BacktestStat(
        signature_key=key,
        symbol="BTCUSDT",
        asset_class=asset,
        engine=engine,
        event_type=event,
        strength_class=strength,
        direction=direction,
        sample_size=40,
        win_1r_pct=win,
        payload={
            "win_1r_ci": ci,
            "unstable": unstable,
            "walk_forward": windows,
            "regimes": regimes or {},
        },
    )


KEY = "liquidity:sweep_low:strong:long:crypto:4h"


def _seed_decaying(repo: MemoryRepository, key: str = KEY) -> None:
    # 장기 70%지만 최근 창은 25%(N=20) → CI 상한 < 70 → 부패.
    repo.upsert_backtest_stat(
        _stat(key, win=70.0, ci=[60.0, 80.0], unstable=False, windows=_windows([(70.0, 20), (25.0, 20)]))
    )


# ── 상태 머신 · 근거 필수 ──────────────────────────────────────────

def test_record_transition_requires_evidence() -> None:
    repo = MemoryRepository()
    with pytest.raises(ValueError):
        record_transition(repo, signature_key=KEY, previous="validated", new="degraded", transition="degrade", reason="x", evidence={}, autonomous=True)


def test_autonomous_recovery_is_forbidden() -> None:
    repo = MemoryRepository()
    with pytest.raises(ValueError):
        record_transition(repo, signature_key=KEY, previous="degraded", new="validated", transition="recover", reason="x", evidence={"win": 1}, autonomous=True)


# ── 자율 강등 → 격리 + 통보 ────────────────────────────────────────

def test_degrade_then_quarantine_autonomous() -> None:
    repo = MemoryRepository()
    _seed_decaying(repo)
    settings = _settings()

    first = run_decay_sweep(repo, settings)
    assert current_state(repo, KEY, settings=settings) == "degraded"
    assert first["autonomous_transitions"][0]["to"] == "degraded"

    # R4: 강등 직후 같은 관측으로는 격리로 점프하지 않는다 (새 데이터 필요).
    same_data = run_decay_sweep(repo, settings)
    assert current_state(repo, KEY, settings=settings) == "degraded"
    assert not same_data["autonomous_transitions"]
    assert same_data["stale_skipped"] and same_data["stale_skipped"][0]["reason"] == "no_new_data_since_degrade"

    # 새 관측(통계 재생성)에서 부패가 재관측되면 격리.
    _seed_decaying(repo)  # generated_at 갱신 = 새 데이터
    second = run_decay_sweep(repo, settings)
    assert current_state(repo, KEY, settings=settings) == "quarantined"
    assert second["autonomous_transitions"][0]["to"] == "quarantined"

    # 통보: 셀프 오딧 "스스로 한 일" 2단에 노출.
    audit = build_self_audit(repo)
    did = audit["engine_did_autonomously"]
    assert did["count"] >= 1
    assert audit["awaiting_approval"]["count"] == 0


# ── 복귀는 제안만, 자동 적용 안 됨 (비대칭) ────────────────────────

def test_recovery_is_proposal_only_not_auto_applied() -> None:
    repo = MemoryRepository()
    settings = _settings()
    # 먼저 자율 강등.
    record_transition(repo, signature_key=KEY, previous="validated", new="degraded", transition="degrade", reason="seed", evidence={"win": 40}, autonomous=True)
    # 이제 회복된 통계: 최근 2창 게이트 재통과, 부패 신호 없음.
    repo.upsert_backtest_stat(
        _stat(KEY, win=82.0, ci=[70.0, 90.0], unstable=False, windows=_windows([(85.0, 30), (84.0, 30)]))
    )

    sweep = run_decay_sweep(repo, settings)
    # 복귀는 제안만 — 상태는 여전히 degraded.
    assert current_state(repo, KEY, settings=settings) == "degraded"
    assert sweep["recovery_proposals"] and sweep["recovery_proposals"][0]["signature_key"] == KEY
    assert not sweep["autonomous_transitions"]

    # 승인 후에만 validated 복귀.
    apply_recovery(repo, KEY, approved_by="tester")
    assert current_state(repo, KEY, settings=settings) == "validated"


# ── 주간 리포트 2단 ────────────────────────────────────────────────

def test_self_audit_two_columns() -> None:
    repo = MemoryRepository()
    _seed_decaying(repo)
    run_decay_sweep(repo, _settings())
    audit = build_self_audit(repo)
    assert "engine_did_autonomously" in audit and "awaiting_approval" in audit
    assert audit["engine_did_autonomously"]["label"].startswith("엔진이")
    assert audit["awaiting_approval"]["label"].startswith("사용자")


# ── autonomy_log 스냅샷 + 오판율 ───────────────────────────────────

def test_autonomy_log_keeps_evidence_and_scorecard() -> None:
    repo = MemoryRepository()
    _seed_decaying(repo)
    run_decay_sweep(repo, _settings())
    logs = repo.list_autonomy_logs(signature_key=KEY)
    assert logs and logs[0].evidence  # 근거 스냅샷 보존
    assert logs[0].evidence.get("signals")

    # 강등 후 복귀 → 오판율 집계에 반영.
    apply_recovery(repo, KEY, approved_by="tester")
    card = autonomy_scorecard(repo)
    assert card["autonomous_downgrades"] >= 1
    assert card["reversed_after_recovery"] >= 1


# ── 주간 상한 · 킬스위치 ───────────────────────────────────────────

def test_weekly_cap_defers_excess_to_proposal() -> None:
    repo = MemoryRepository()
    for i in range(3):
        _seed_decaying(repo, key=f"liquidity:sweep_low:strong:long:crypto:{i}h")
    sweep = run_decay_sweep(repo, _settings(autonomy_weekly_transition_cap=1))
    assert len(sweep["autonomous_transitions"]) == 1
    assert len(sweep["deferred_to_proposal"]) == 2
    assert all(row["reason"] == "weekly_cap" for row in sweep["deferred_to_proposal"])


def test_killswitch_converts_all_to_proposal() -> None:
    repo = MemoryRepository()
    _seed_decaying(repo)
    sweep = run_decay_sweep(repo, _settings(autonomy_enabled=False))
    assert not sweep["autonomous_transitions"]
    assert sweep["deferred_to_proposal"] and sweep["deferred_to_proposal"][0]["reason"] == "killswitch"
    # 상태 불변.
    assert current_state(repo, KEY, settings=_settings()) in {"validated", "candidate"}


def test_all_quarantined_triggers_critical_freeze() -> None:
    repo = MemoryRepository()
    settings = _settings()
    record_transition(repo, signature_key=KEY, previous="degraded", new="quarantined", transition="quarantine", reason="seed", evidence={"win": 20}, autonomous=True)
    _seed_decaying(repo)  # a validated one that would decay
    sweep = run_decay_sweep(repo, settings)
    assert sweep["critical"] is True
    assert sweep["frozen"] is True
    # 동결 중 신규 자율 강등 없음.
    assert not sweep["autonomous_transitions"]


# ── 게이트 소비 (강등 제외) ────────────────────────────────────────

def test_gate_excludes_degraded_signature() -> None:
    settings = Settings()
    good_stat = {"sample_size": 40, "win_1r_pct": 75.0, "win_1r_ci": [60.0, 87.5]}
    kwargs = dict(
        confidence=80,
        stat=good_stat,
        quote_volume_24h=5_000_000,
        asset_class="crypto",
        earnings_blocked=False,
        daily_room=True,
        cooldown_active=False,
    )
    assert evaluate_discovery_gate(settings, signature_state="validated", **kwargs).quality_passed is True
    result = evaluate_discovery_gate(settings, signature_state="degraded", **kwargs)
    assert result.quality_passed is False
    assert any(r["code"] == "signature_lifecycle_state" and not r["passed"] for r in result.reasons)


# ── 전수 감사 수정 회귀 ────────────────────────────────────────────

def test_decay_uses_largest_sample_stat_per_signature() -> None:
    # 소표본 알트의 최신 unstable 통계가 BTC 대표본 통계를 밀어내 전역 강등하면 안 된다.
    repo = MemoryRepository()
    healthy = _stat(KEY, win=70.0, ci=[58.0, 80.0], unstable=False, windows=_windows([(70.0, 30), (69.0, 30)]))
    repo.upsert_backtest_stat(healthy)  # BTC, N=40
    tiny = _stat(KEY, win=40.0, ci=[20.0, 60.0], unstable=True, windows=_windows([(40.0, 6)]))
    tiny = tiny.model_copy(update={"symbol": "TINYUSDT", "sample_size": 12})
    repo.upsert_backtest_stat(tiny)  # 알트, N=12 — 더 최신

    sweep = run_decay_sweep(repo, _settings())
    assert not sweep["autonomous_transitions"]  # 대표본(N=40) 기준 판정 → 안정


def test_single_quarantine_does_not_trigger_engine_distrust() -> None:
    # 로그 있는 시그니처만 분모로 쓰면 첫 격리 1건 = 전면 불신 오탐.
    repo = MemoryRepository()
    record_transition(
        repo, signature_key="liquidity:sweep_low:strong:long:crypto:1d",
        previous="degraded", new="quarantined", transition="quarantine",
        reason="seed", evidence={"win": 20}, autonomous=True,
    )
    # 로그 없는 건강한 시그니처가 존재.
    repo.upsert_backtest_stat(_stat(KEY, win=70.0, ci=[58.0, 80.0], unstable=False, windows=_windows([(70.0, 30), (69.0, 30)])))
    sweep = run_decay_sweep(repo, _settings())
    assert sweep["all_quarantined"] is False
    assert sweep["frozen"] is False


def test_repeated_sweep_does_not_spam_proposals() -> None:
    repo = MemoryRepository()
    _seed_decaying(repo)
    settings = _settings(autonomy_enabled=False)  # 킬스위치 → 제안 모드
    run_decay_sweep(repo, settings)
    run_decay_sweep(repo, settings)
    proposals = [log for log in repo.list_autonomy_logs(signature_key=KEY) if log.transition == "degrade_proposed"]
    assert len(proposals) == 1  # 중복 기록 없음


# ── 레짐별 부분 부패 ───────────────────────────────────────────────

def test_regime_partial_decay_tags_regime() -> None:
    # 전체는 멀쩡(최근 창 양호)하지만 하락추세 슬라이스만 CI 상한 < 전체 점추정.
    stat = _stat(
        KEY,
        win=70.0,
        ci=[60.0, 80.0],
        unstable=False,
        windows=_windows([(72.0, 30), (71.0, 30)]),
        regimes={"downtrend": {"sample_size": 20, "win_1r_pct": 30.0, "win_1r_ci": [15.0, 48.0], "regime_label": "하락추세"}},
    )
    decision = evaluate_signature(stat.model_dump()["payload"] | {"win_1r_pct": 70.0, "sample_size": 40}, current="validated")
    assert decision["action"] == "degrade"
    assert decision["regime"] == "downtrend"
