"""WO-FCE-POLY-STATUS-01 — 폴리 트랙 상태 표시 계약.

이 WO 는 **표시만** 고친다. 판정(`STRUCTURALLY_BLOCKED`)은 이미 옳고, 화면이 그것을
말하지 않는 것이 문제였다. 아래 테스트는 그 표시가 다시 흐려지는 경로를 막는다.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.poly_paper import track_status as ts
from app.validation.pending_decisions import BLOCKING, pending_decisions

REPO_ROOT = Path(__file__).resolve().parents[2]

# C3·C5 — 판정 로직과 다른 트랙은 건드리지 않는다.
UNTOUCHABLE = ("backend/app/validation/sample_viability.py", "backend/app/paper/policy.py", "backend/app/stock_paper", "backend/app/analyst")


# ── 2-1 · 451 을 판정된 상태로 ──────────────────────────────────────────


def test_451_is_classified_as_geo_blocked_and_not_retryable() -> None:
    """`451` 은 네트워크 오류가 아니다. 재시도로 풀리지 않는다는 것이 핵심 정보다."""
    result = ts.classify_collection("error", "HTTPStatusError: Client error '451 Unavailable For Legal Reasons' for url '...'")
    assert result["status"] == ts.STATUS_GEO_BLOCKED
    assert result["retryable"] is False
    assert "451" in result["label"] and "지역 제한" in result["label"]


def test_geo_block_advice_does_not_suggest_a_bypass() -> None:
    """C1 — 법적 차단이다. 프록시·VPN 을 권하지 않는다."""
    advice = ts.classify_collection("error", "451 Unavailable For Legal Reasons")["advice"]
    for forbidden in ("프록시", "VPN", "우회 방법", "bypass", "proxy"):
        assert forbidden not in advice
    assert "우회하지 않는다" in advice


def test_other_errors_stay_retryable() -> None:
    """모든 오류를 차단으로 부르면 분류의 의미가 없다."""
    result = ts.classify_collection("error", "ReadTimeout: timed out")
    assert result["status"] == ts.STATUS_TRANSIENT
    assert result["retryable"] is True


def test_ok_status_is_not_dressed_as_an_error() -> None:
    result = ts.classify_collection("ok", None)
    assert result["status"] == ts.STATUS_OK
    assert result["detail"] is None


def test_raw_exception_moves_to_detail_not_the_headline() -> None:
    """2-1 항목 3 — 원시 예외는 상세 보기로 내린다."""
    raw = "HTTPStatusError: Client error '451 Unavailable For Legal Reasons' for url 'https://gamma-api...'"
    result = ts.classify_collection("error", raw)
    assert "HTTPStatusError" not in result["label"]
    assert "HTTPStatusError" in result["detail"]


# ── 2-1 · STRUCTURALLY_BLOCKED 를 표시한다 ─────────────────────────────


def test_structural_block_is_surfaced_with_its_reason() -> None:
    """D4 — 판정은 이미 있었고 화면이 표시하지 않았다."""
    status = ts.track_status(
        collection=ts.classify_collection("error", "451 Unavailable For Legal Reasons"),
        viability={"verdict": "STRUCTURALLY_BLOCKED", "verdict_reason": "보유 8건 전부 검증 종료 이후 만기"},
        expiry={"sample_possible": False, "label": "정산 대기 8건"},
    )
    assert status["structurally_blocked"] is True
    assert status["verdict"] == "STRUCTURALLY_BLOCKED"
    assert "만기" in status["verdict_reason"]
    assert status["headline"] == "구조적 검증 불가 + 수집 차단"


def test_restart_is_stated_not_to_help() -> None:
    """C4 — "재시작하면 되겠지"로 읽히면 안 된다."""
    status = ts.track_status(
        collection=ts.classify_collection("error", "451 Unavailable For Legal Reasons"),
        viability={"verdict": "STRUCTURALLY_BLOCKED", "verdict_reason": "x"},
        expiry={"sample_possible": False},
    )
    assert status["restart_resolves"] is False
    assert "재시작해도 해소되지 않는다" in status["restart_note"]


def test_expiry_alone_marks_a_structural_block() -> None:
    """수집이 정상이어도 만기가 창 밖이면 구조적 불가다."""
    status = ts.track_status(collection=ts.classify_collection("ok", None), viability=None, expiry={"sample_possible": False})
    assert status["structurally_blocked"] is True
    assert status["headline"] == "구조적 검증 불가"


def test_healthy_track_is_not_flagged() -> None:
    status = ts.track_status(collection=ts.classify_collection("ok", None), viability={"verdict": "VIABLE"}, expiry={"sample_possible": True})
    assert status["structurally_blocked"] is False
    assert status["restart_note"] is None
    assert status["headline"] == "관측 진행"


# ── 2-2 · 숫자의 정체 ──────────────────────────────────────────────────


def test_calibration_samples_are_labelled_as_not_ours() -> None:
    """`N=12774` 를 우리 표본처럼 보여주면 안 된다 — 보유는 9건이다."""
    labels = ts.sample_labels(resolution_count=12774, our_positions=9, settling_within_validation=0)
    assert labels["calibration_samples"] == 12774
    assert "우리 거래 표본이 아니다" in labels["calibration_label"]
    assert labels["our_positions"] == 9


def test_our_validation_sample_is_reported_as_zero() -> None:
    """C4 — 0 이면 0 이다."""
    labels = ts.sample_labels(resolution_count=12774, our_positions=9, settling_within_validation=0)
    assert labels["our_validation_samples"] == 0
    assert "검증 표본" in labels["our_validation_label"]


def test_numerator_denominator_mismatch_is_named() -> None:
    """청산 완료율 141,933% 가 그 증거다. 판정 수정은 범위 밖이므로 사실만 적는다."""
    note = ts.sample_labels(resolution_count=12774, our_positions=9, settling_within_validation=0)["mismatch_note"]
    assert "141,933%" in note
    assert "C3" in note


# ── 2-2 · 시계 0 의 사유 분해 ──────────────────────────────────────────


def _rows() -> list[dict]:
    return [{"day": f"2026-08-{day:02d}", "valid": 0, "reason": "커버리지 36.46% < 임계 90.0%"} for day in range(13, 20)] + [
        {"day": f"2026-08-{day:02d}", "valid": 0, "reason": "관측 0건 (정지)"} for day in range(20, 27)
    ]


def test_clock_zero_is_broken_down_by_cause() -> None:
    """`유실 12일` 만으로는 무엇을 고쳐야 하는지 알 수 없다."""
    result = ts.clock_breakdown(_rows(), window_start="2026-08-13")
    assert result["valid_days"] == 0
    assert result["stalled_days"] == 7
    assert result["thin_coverage_days"] == 7
    assert "수집 정지 7일" in result["label"] and "커버리지 미달 7일" in result["label"]


def test_days_before_the_window_are_excluded() -> None:
    """창 밖 날짜를 세면 시계가 창과 어긋난다."""
    rows = [{"day": "2026-07-01", "valid": 1, "reason": None}, *_rows()]
    assert ts.clock_breakdown(rows, window_start="2026-08-13")["days_counted"] == 14


def test_breakdown_says_why_the_split_matters() -> None:
    note = ts.clock_breakdown(_rows(), window_start="2026-08-13")["note"]
    assert "조치가 다르다" in note


# ── 2-3 · 처리 방침 결정 ───────────────────────────────────────────────


def test_disposition_decision_appears_at_blocking_severity() -> None:
    items = pending_decisions(gate_approved=False, sleep_guard={}, poly_blocked={"structurally_blocked": True, "collection_status": "geo_blocked"})
    item = next((row for row in items if row["id"] == "poly_track_disposition"), None)
    assert item is not None
    assert item["severity"] == BLOCKING


def test_disposition_states_the_451_dependency() -> None:
    """**451 이 안 풀리면 B 도 불가능하다.** 그 사실이 결정 자료에 있어야 한다."""
    items = pending_decisions(gate_approved=False, sleep_guard={}, poly_blocked={"structurally_blocked": True})
    item = next(row for row in items if row["id"] == "poly_track_disposition")
    assert "451 이 안 풀리면 B 도 불가능하다" in item["detail"]
    assert "A 와 C 뿐" in item["detail"]
    assert "우회하지 않는다" in item["blocked_by"]


def test_decision_is_absent_when_the_track_is_not_blocked() -> None:
    """막히지 않았는데 결정을 띄우면 소음이다."""
    items = pending_decisions(gate_approved=False, sleep_guard={})
    assert not any(row["id"] == "poly_track_disposition" for row in items)


# ── 제약 증명 ──────────────────────────────────────────────────────────


def test_no_bypass_path_was_added() -> None:
    """C1 — 프록시·VPN·우회 경로 금지."""
    source = (REPO_ROOT / "backend/app/poly_paper/track_status.py").read_text(encoding="utf-8")
    for forbidden in ("proxy", "socks", "http_proxy", "verify=False"):
        assert forbidden not in source.lower()


def test_status_module_makes_no_judgement_of_its_own() -> None:
    """C3 — 판정은 `sample_viability` 가 한다. 이 모듈은 읽어서 표시한다."""
    source = (REPO_ROOT / "backend/app/poly_paper/track_status.py").read_text(encoding="utf-8")
    assert "STRUCTURALLY_BLOCKED" in source, "판정 값을 읽기는 한다"
    # 판정을 새로 계산하는 경로가 없어야 한다.
    for computing in ("def classify_verdict", "TARGET_SAMPLES", "effective_days >"):
        assert computing not in source


def test_judgement_and_other_tracks_are_untouched() -> None:
    """C3·C5 — 판정 로직·다른 트랙 diff 0줄."""
    diff = subprocess.run(["git", "diff", "origin/main", "--stat", "--", *UNTOUCHABLE], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if diff.returncode != 0:
        pytest.skip("origin/main 을 참조할 수 없는 환경")
    assert diff.stdout.strip() == "", f"C3·C5 위반:\n{diff.stdout}"


# ── WO-FCE-DEFAULTS-01 1-2 · 검증 대상 제외 (임시값) ────────────────────


class _ScopeSettings:
    validation_exclude_poly = True


class _ScopeOff:
    validation_exclude_poly = False


def test_poly_is_excluded_from_validation_scope() -> None:
    from app.validation import track_scope

    status = track_scope.track_scope_status(track_scope.TRACK_POLY, _ScopeSettings())
    assert status["excluded"] is True
    assert status["in_validation_scope"] is False
    assert "451" in status["reason"]


def test_exclusion_is_labelled_provisional() -> None:
    """C5 — 확정값처럼 보이면 안 된다."""
    from app.validation import track_scope

    status = track_scope.track_scope_status(track_scope.TRACK_POLY, _ScopeSettings())
    assert "임시값" in status["label"]
    assert status["revert"] == "FCE_VALIDATION_EXCLUDE_POLY=false"


def test_exclusion_reverts_with_one_setting() -> None:
    """C4 — 전부 원복 가능."""
    from app.validation import track_scope

    assert track_scope.track_scope_status(track_scope.TRACK_POLY, _ScopeOff())["excluded"] is False
    assert track_scope.excluded_tracks(_ScopeOff()) == frozenset()


def test_other_tracks_are_never_excluded() -> None:
    """C5(원 WO) — 다른 트랙 무영향."""
    from app.validation import track_scope

    for track in ("crypto", "stock_kr", "stock_us", "whale_follow"):
        assert track_scope.track_scope_status(track, _ScopeSettings())["excluded"] is False


def test_scope_block_states_that_data_is_kept() -> None:
    """C2(원 WO) — 원장 보존. 제외는 삭제가 아니다."""
    from app.validation import track_scope

    block = track_scope.scope_block(_ScopeSettings())
    assert "데이터를 버리는 것이 아니다" in block["data_kept"]
    assert "막고 있지 않았다" in block["measured_note"]


def test_disposition_decision_is_transitioned_not_deleted() -> None:
    """4-1 항목 3 — 삭제하지 않는다. 사용자가 확정할 대상이다."""
    from app.validation.pending_decisions import PROVISIONAL

    items = pending_decisions(gate_approved=False, sleep_guard={}, poly_blocked={"structurally_blocked": True, "provisional_applied": "A(제외)"})
    item = next(row for row in items if row["id"] == "poly_track_disposition")
    assert item["severity"] == PROVISIONAL
    assert item["provisional_applied"] == "A(제외)"
    assert item["revert"] == "FCE_VALIDATION_EXCLUDE_POLY=false"
