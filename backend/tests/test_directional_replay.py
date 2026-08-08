"""WO-FCE-DIRECTIONAL-INTEGRITY-01 Phase 0 — 재판정 하네스 회귀.

고정하는 명제:
1. **룩어헤드 부재** — 입력을 절반으로 잘라도 그 절반의 판정이 전체 입력 시와 완전히 동일하다
2. **판정 로직 무변경** — 이 Phase 에서 `app/analyst/` · `app/structure/` 판정 코드 diff 0줄
3. 커버리지·침묵 가중·불일치·선행 추세 분포가 산출된다
4. 기준선 해시가 판정이 조용히 바뀌지 않았음을 증명한다
5. 어떤 분포 함수도 **임계를 정하지 않는다** (결정 3은 사람이 한다)
"""

from __future__ import annotations

import math
import random
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.analyst.confluence import ENGINE_BASE_WEIGHTS
from app.db.models import MarketCandle
from app.validation import directional_replay as dr

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _candles(count: int = 150, seed: int = 7) -> list[MarketCandle]:
    """마크다운 뒤 박스 — 증상 A(SPCXUSDT)의 형태를 합성으로 재현한다."""
    rng = random.Random(seed)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles: list[MarketCandle] = []
    price = 200.0
    for index in range(count):
        drift = -1.4 if index < count * 0.45 else 0.0
        price = max(5.0, price + drift + rng.uniform(-1.2, 1.2) + 2.5 * math.sin(index / 6))
        candles.append(
            MarketCandle(
                timestamp=start + timedelta(hours=4 * index),
                open=price,
                high=price + abs(rng.gauss(0, 1.0)) + 0.4,
                low=price - abs(rng.gauss(0, 1.0)) - 0.4,
                close=price,
                volume=1_000 + rng.random() * 900,
            )
        )
    return candles


@pytest.fixture(scope="module")
def records() -> list[dict]:
    return dr.replay_judgments(symbol="TESTUSDT", timeframe="4h", candles=_candles())


# ── 1. 룩어헤드 부재 (C4) ───────────────────────────────────────────────


def test_prefix_invariance_proves_no_lookahead(records: list[dict]) -> None:
    """구간 절반만 입력해도 그 절반의 판정이 전체 입력 시와 **완전히 동일**해야 한다.

    미래 캔들이 한 줄이라도 새면 뒤쪽 봉이 앞쪽 판정을 바꾸므로 이 등식이 깨진다.
    등식이 깨진 채로 낸 대조표는 전부 무효다.
    """
    # 분석 창이 100봉이므로 절단점은 그보다 커야 한다 — 그래야 겹치는 판정 지점이 생긴다.
    truncated = _candles()[:125]
    partial = dr.replay_judgments(symbol="TESTUSDT", timeframe="4h", candles=truncated)

    assert partial, "절반 입력에서도 판정 지점이 나와야 비교가 성립한다"
    overlap = {record["as_of"] for record in partial} & {record["as_of"] for record in records}
    assert overlap, "겹치는 시점이 없으면 이 테스트는 아무것도 증명하지 않는다"

    full_by_time = {record["as_of"]: record for record in records}
    for record in partial:
        if record["as_of"] in full_by_time:
            assert record == full_by_time[record["as_of"]], f"{record['as_of']} 판정이 입력 길이에 따라 달라졌다 — 룩어헤드"


def test_replay_is_deterministic(records: list[dict]) -> None:
    again = dr.replay_judgments(symbol="TESTUSDT", timeframe="4h", candles=_candles())

    assert dr.records_digest(again) == dr.records_digest(records)


# ── 2. 판정 로직 무변경 증명 ─────────────────────────────────────────────


def test_phase0_does_not_touch_judgment_logic() -> None:
    """Phase 0 은 계측 전용이다. 판정 디렉터리에 diff 가 있으면 기준선이 기준선이 아니다."""
    diff = subprocess.run(
        ["git", "diff", "origin/main", "--stat", "--", "backend/app/analyst/", "backend/app/structure/"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if diff.returncode != 0:
        pytest.skip("origin/main 을 참조할 수 없는 환경")

    assert diff.stdout.strip() == "", f"판정 로직이 변경됐다:\n{diff.stdout}"


def test_harness_lives_outside_judgment_packages() -> None:
    assert (BACKEND_ROOT / "app" / "validation" / "directional_replay.py").exists()
    source = (BACKEND_ROOT / "app" / "validation" / "directional_replay.py").read_text(encoding="utf-8")
    # 하네스는 판정을 재구현하지 않고 **호출**한다. 판정 내부 함수를 끌어다 쓰기 시작하면
    # 기준선이 라이브가 아니라 하네스의 해석을 대변하게 된다.
    assert "build_confluence" in source and "build_chart_analysis" in source
    assert "wyckoff.engine" not in source, "와이코프 내부를 직접 호출하면 라이브 경로와 갈라진다"
    assert "_phase_from_events" not in source and "_trend_payload(" not in source


# ── 3. 분포 산출 ────────────────────────────────────────────────────────


def test_coverage_distribution_covers_every_bucket(records: list[dict]) -> None:
    coverage = dr.coverage_distribution(records)

    assert coverage["engine_total"] == len(ENGINE_BASE_WEIGHTS) == 9
    assert [item["judged_engines"] for item in coverage["buckets"]] == list(range(10))
    assert sum(item["count"] for item in coverage["buckets"]) == len(records)


def test_silent_weight_distribution_uses_the_specified_cutpoints(records: list[dict]) -> None:
    silent = dr.silent_weight_distribution(records)

    assert [item["range"] for item in silent["buckets"]] == ["0%", "~10%", "~25%", "~40%", "40%+"]
    assert sum(item["count"] for item in silent["buckets"]) == len(records)
    assert silent["engine_weight_total"] == 108.0


def test_engine_silence_frequency_names_every_engine(records: list[dict]) -> None:
    frequency = dr.engine_silence_frequency(records)

    assert {item["engine"] for item in frequency["engines"]} == set(ENGINE_BASE_WEIGHTS)


def test_records_expose_every_measured_axis(records: list[dict]) -> None:
    required = {
        "wyckoff_phase",
        "wyckoff_side",
        "trend_direction",
        "prior_trend_conflict",
        "judged_engine_count",
        "silent_weight_pct",
        "directional_evidence_count",
        "directional_engine_count",
        "long_score",
        "short_score",
        "margin_ratio",
        "raw_stance",
        "stance",
        "wyckoff_liquidity_conflict",
    }

    assert records, "판정 지점이 0건이면 이 WO 의 어떤 Phase 도 측정할 수 없다"
    assert required <= set(records[0])


def test_silent_weight_is_the_complement_of_judged_engines(records: list[dict]) -> None:
    for record in records:
        judged = set(record["judged_engines"])
        silent = set(record["silent_engines"])
        assert judged | silent == set(ENGINE_BASE_WEIGHTS)
        assert judged & silent == set()
        assert record["silent_weight"] == pytest.approx(sum(ENGINE_BASE_WEIGHTS[name] for name in silent))


# ── 4. 파생 지표의 의미 ─────────────────────────────────────────────────


def test_prior_trend_conflict_marks_the_inverted_causality() -> None:
    # 마크다운 뒤 분산 · 마크업 뒤 축적 — 정석상 인과가 뒤집힌 조합만 참이어야 한다.
    assert dr._trend_conflict("bearish", "distribution") is True
    assert dr._trend_conflict("bullish", "accumulation") is True
    assert dr._trend_conflict("bearish", "accumulation") is False
    assert dr._trend_conflict("bullish", "distribution") is False
    assert dr._trend_conflict("neutral", "distribution") is False


def test_engine_stances_separate_net_direction_from_internal_conflict() -> None:
    confluence = {
        "long_evidence": [{"engine": "level", "score": 9.0}, {"engine": "wyckoff", "score": 4.0}],
        "short_evidence": [{"engine": "level", "score": 3.0}],
    }

    engines = dr.engine_stances(confluence)

    # level 은 지지·저항을 동시에 낸다 — 합쳐 버리면 "항상 충돌"이라는 무의미한 결론만 남는다.
    assert engines["level"]["net"] == "long"
    assert engines["level"]["internal_conflict"] is True
    assert engines["wyckoff"]["net"] == "long"
    assert engines["wyckoff"]["internal_conflict"] is False
    assert engines["harmonic"]["stance"] == "silent"


def test_wyckoff_liquidity_disagreement_counts_only_opposed_pairs() -> None:
    base = {"wyckoff_stance": "short", "liquidity_stance": "long", "wyckoff_liquidity_conflict": True}
    agree = {"wyckoff_stance": "long", "liquidity_stance": "long", "wyckoff_liquidity_conflict": False}
    silent = {"wyckoff_stance": "silent", "liquidity_stance": "long", "wyckoff_liquidity_conflict": False}

    result = dr.wyckoff_liquidity_disagreement([base, agree, silent])

    assert result["both_directional"] == 2
    assert result["conflict_count"] == 1
    assert result["conflict_pct_of_both_directional"] == 50.0
    assert "표본 부족" in result["sample_warning"]


def test_evidence_vs_engine_gap_finds_single_engine_sufficiency() -> None:
    single = {"directional_evidence_count": 4, "directional_engine_count": 1, "min_directional_evidence": 3}
    spread = {"directional_evidence_count": 4, "directional_engine_count": 3, "min_directional_evidence": 3}

    result = dr.evidence_vs_engine_gap([single, spread])

    assert result["meets_item_threshold"] == 2
    assert result["single_engine_count"] == 1
    assert result["single_engine_pct_of_met"] == 50.0


def test_stance_split_counts_opposed_directions_separately() -> None:
    opposed = {"stance": "short_leaning", "raw_stance": "long_leaning", "stance_disagrees_with_raw": True}
    softened = {"stance": "short_leaning", "raw_stance": "conflicted", "stance_disagrees_with_raw": True}
    aligned = {"stance": "long_leaning", "raw_stance": "long_leaning", "stance_disagrees_with_raw": False}

    result = dr.stance_split_frequency([opposed, softened, aligned])

    assert result["split_count"] == 2
    assert result["opposed_count"] == 1


# ── 5. 기준선 ───────────────────────────────────────────────────────────


def test_baseline_snapshot_is_hash_pinned(records: list[dict]) -> None:
    snapshot = dr.baseline_snapshot(records, symbol="TESTUSDT", timeframe="4h")

    assert snapshot["records_sha256"] == dr.records_digest(records)
    assert dr.compare_to_baseline(snapshot, records)["identical"] is True


def test_compare_to_baseline_reports_direction_of_change(records: list[dict]) -> None:
    snapshot = dr.baseline_snapshot(records, symbol="TESTUSDT", timeframe="4h")
    softened = [{**record, "stance": "insufficient"} for record in records]

    comparison = dr.compare_to_baseline(snapshot, softened)

    assert comparison["identical"] is False
    assert comparison["directional_candidate"] == 0
    # 보수화(C3)는 방향 판정이 줄어드는 방향이다 — 늘어나면 그 자체가 경보다.
    assert comparison["monotone_decrease"] is True
    assert comparison["directional_delta"] <= 0


# ── 6. 임계를 정하지 않는다 ─────────────────────────────────────────────


def test_harness_defines_no_thresholds(records: list[dict]) -> None:
    """Phase 0 은 분포만 낸다. 여기서 임계를 정하면 결정 3이 사후 합리화가 된다."""
    coverage = dr.coverage_distribution(records)
    silent = dr.silent_weight_distribution(records)

    for payload in (coverage, silent):
        assert "threshold" not in payload
        assert not any(key.endswith("_min") or key.endswith("_max") for key in payload)
    assert "임계 미정" in coverage["note"]


def test_summary_states_sample_insufficiency_below_thirty() -> None:
    assert dr.summarize([])["sample_warning"] is not None
    assert "표본 부족" in dr.summarize([])["sample_warning"]


# ── 7. 문서 ─────────────────────────────────────────────────────────────


def test_replay_harness_document_records_the_lookahead_proof() -> None:
    text = (REPO_ROOT / "docs" / "validation" / "REPLAY_HARNESS.md").read_text(encoding="utf-8")

    for item in ("룩어헤드", "기준선", "prefix", "stance_history_candles"):
        assert item in text, f"하네스 문서에 '{item}' 이 없다"


def test_coverage_document_leaves_thresholds_to_the_user() -> None:
    text = (REPO_ROOT / "docs" / "validation" / "DIRECTIONAL_COVERAGE.md").read_text(encoding="utf-8")

    for item in ("사용자 결정", "판정 가능 엔진", "침묵 가중", "MIN_DIRECTIONAL_EVIDENCE"):
        assert item in text, f"커버리지 문서에 '{item}' 이 없다"
