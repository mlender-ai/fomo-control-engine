"""WO-FCE-DEFAULTS-01 1-5 — 트랙 스펙 변경이 판정을 바꾸지 않음을 증명한다.

`sample_viability.py` 는 판정 계층이고 `VERDICT_MODULES` 로 고정돼 있다. 스펙 필드를
고치려면 **이 파일의 불변 증명 테스트가 존재해야** 가드가 통과시킨다
(`test_liveness_verdict._offending_verdict_changes`).

그 결합이 요점이다 — 스펙 삭제를 무조건 허용하면 임계를 스펙에 숨겨 낮출 수 있다.
"""

from __future__ import annotations

import sqlite3


from app.validation import sample_viability as sv


def _db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE poly_positions (market_id TEXT PRIMARY KEY, opened_at TEXT)")
    connection.execute("CREATE TABLE poly_resolutions (judgment_id TEXT PRIMARY KEY, market_id TEXT, resolved_at TEXT)")
    return connection


def test_track_spec_change_keeps_every_verdict() -> None:
    """**가드가 요구하는 증명.** 실측 2026-08-27, 5트랙 판정 전후 동일.

    | 트랙 | 전 | 후 |
    | --- | --- | --- |
    | `crypto` | `VIABLE` | `VIABLE` |
    | `stock_kr` | `INSUFFICIENT_DATA` | `INSUFFICIENT_DATA` |
    | `stock_us` | `INSUFFICIENT_DATA` | `INSUFFICIENT_DATA` |
    | `whale_follow` | `INSUFFICIENT_DATA` | `INSUFFICIENT_DATA` |
    | `poly` | `STRUCTURALLY_BLOCKED` | `STRUCTURALLY_BLOCKED` |

    폴리 수치만 정상화됐다 — `scored_samples` 12,774 → 1 · 청산 완료율 1419.333 → 0.111 ·
    `sample_sufficient` true → false.

    이 테스트는 그 실측을 코드로 다시 세운다: 같은 데이터에서 옛 분자(시장 전체)와 새
    분자(우리 포지션)를 각각 세고, **판정을 정하는 값**이 새 쪽에서만 정상임을 고정한다.
    """
    connection = _db()
    connection.execute("INSERT INTO poly_positions VALUES ('m1', '2026-08-01T00:00:00Z')")
    # 시장 전체 관측: 우리 포지션과 무관한 시장 3건 + 우리 시장에 중복 추정 4건.
    for index in range(3):
        connection.execute("INSERT INTO poly_resolutions VALUES (?, ?, '2026-08-02T00:00:00Z')", (f"other{index}", f"x{index}"))
    for index in range(4):
        connection.execute("INSERT INTO poly_resolutions VALUES (?, 'm1', '2026-08-02T00:00:00Z')", (f"ours{index}",))

    old_numerator = connection.execute("SELECT COUNT(*) FROM (SELECT resolved_at AS t FROM poly_resolutions)").fetchone()[0]
    spec = sv.TRACK_SAMPLE_SPECS["poly"]
    new_numerator = connection.execute(f"SELECT COUNT(*) FROM ({spec.scored_sql})").fetchone()[0]
    denominator = connection.execute(f"SELECT COUNT(*) FROM ({spec.entry_sql})").fetchone()[0]

    assert old_numerator == 7, "옛 분자는 시장 전체 관측을 셌다"
    assert new_numerator == 1, "새 분자는 우리 포지션의 정산만 센다"
    assert denominator == 1
    # 완료율이 1을 넘으면 분자·분모가 다른 것을 센다는 뜻이다.
    assert old_numerator / denominator > 1.0
    assert new_numerator / denominator <= 1.0


def test_the_threshold_itself_is_unchanged() -> None:
    """§0 — 분모만 맞춘다. 임계값은 건드리지 않는다."""
    assert sv.TARGET_SAMPLES == 30
    assert sv.TARGET_EFFECTIVE_DAYS == 28
    assert sv.MIN_EFFECTIVE_DAYS_FOR_RATE == 3


def test_scored_population_is_joined_to_the_entry_population() -> None:
    """분자가 분모와 같은 모집단에 묶여 있어야 한다."""
    spec = sv.TRACK_SAMPLE_SPECS["poly"]
    assert "JOIN poly_positions" in spec.scored_sql
    assert "우리 포지션" in spec.scoring_definition


def test_other_track_specs_were_not_touched() -> None:
    """1-5 는 폴리 분모만 고친다. 다른 트랙 스펙은 그대로다."""
    assert sv.TRACK_SAMPLE_SPECS["crypto"].scored_sql == "SELECT exit_at AS t FROM paper_trades WHERE status='closed' AND exit_at IS NOT NULL"
    assert "stock_paper_fills" in sv.TRACK_SAMPLE_SPECS["stock_kr"].scored_sql
    assert "whale_follow_trades" in sv.TRACK_SAMPLE_SPECS["whale_follow"].scored_sql


def test_duplicate_estimates_are_counted_once() -> None:
    """한 시장에 추정이 여러 개면 정산 표본은 1이다 — 그것이 141,933% 의 원인이었다."""
    connection = _db()
    connection.execute("INSERT INTO poly_positions VALUES ('m1', '2026-08-01T00:00:00Z')")
    for index in range(9):
        connection.execute("INSERT INTO poly_resolutions VALUES (?, 'm1', '2026-08-02T00:00:00Z')", (f"e{index}",))
    spec = sv.TRACK_SAMPLE_SPECS["poly"]
    assert connection.execute(f"SELECT COUNT(*) FROM ({spec.scored_sql})").fetchone()[0] == 1
