"""WO-FCE-SAMPLE-VIABILITY-01 PHASE 1·6 — 표본 생성 능력 판정.

## 왜 이 모듈이 생겼나

직전 WO(관측 무결성)가 "유효 관측일"을 정직하게 세는 장치를 만들었다. 그 결과가
**우리 인식이 틀렸다**는 것이었다 — 크립토 14/28, 폴리 7/28, KR 5/28, US 1/28.

그런데 더 나쁜 사실이 딸려 나왔다. **유효 관측일을 다 채워도 검증이 되지 않는 트랙이 있다.**
폴리는 보유 9건 중 8건이 2027-01-01 만기다(실측 2026-08-05). 28일을 다 채워도 검증 창 안에
정산되는 건이 0이므로 **채점 가능한 표본이 0**이다.

> **관측일은 검증의 필요조건이지 충분조건이 아니다. 채점 가능한 표본이 나와야 검증이다.**

이 모듈은 그래서 "며칠 봤는가"가 아니라 **"이 트랙이 검증 창 안에서 채점 가능한 표본을
만들 수 있는가"** 를 판정한다.

## 무엇을 세는가

| 트랙 | 진입(분자) | 채점 가능 표본 |
| --- | --- | --- |
| `crypto` | `paper_trades` 신규 | `status='closed'` (청산 완료) |
| `stock_kr`/`stock_us` | `stock_paper_fills` 매수 | `stock_paper_fills` 매도 (청산 완료) |
| `poly` | `poly_positions` 신규 | `poly_resolutions` (만기 정산 = Brier 채점) |

진입률의 분모는 **유효 관측일**이다. 달력일로 나누면 정지 구간이 진입률을 희석해 또 거짓이 된다.

## 금지

- 실적이 0인 트랙을 유니버스 크기로 추정하기 (0은 0으로 적는다)
- N<30 에서 승률·수익률을 계산하기 (표본 부족이라고 쓴다)
- 판정을 통과시키려고 목표 표본 수를 낮추기
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from app.worker import observation

# 통계적 판정이 가능해지는 최소 표본. 도메인 불변("정직한 표본")의 N<30 유보 기준과 같은 수다.
TARGET_SAMPLES = 30
# 4주 검증 목표 유효 관측일. observation 모듈과 같은 수를 쓴다(따로 두면 갈라진다).
TARGET_EFFECTIVE_DAYS = observation.VALIDATION_TARGET_DAYS
# 완료 조건 3: 표본이 최소 몇 개의 시장 국면을 포함해야 하는가.
TARGET_REGIMES = 2
# 이 아래면 비율 자체를 신뢰할 수 없다 — 판정을 내리지 않고 INSUFFICIENT_DATA 로 둔다.
MIN_EFFECTIVE_DAYS_FOR_RATE = 3

VERDICT_VIABLE = "VIABLE"
VERDICT_SLOW = "SLOW"
VERDICT_STRUCTURALLY_BLOCKED = "STRUCTURALLY_BLOCKED"
VERDICT_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class SampleSpec:
    """트랙별 '진입'과 '채점 가능 표본'의 증거.

    두 쿼리 모두 **하나의 타임스탬프 컬럼만** 돌려준다. 판정에 필요한 것은 건수와 날짜뿐이고,
    성적(승률·수익률)은 이 모듈이 계산하지 않는다 — N<30 에서 계산하면 규칙 2 위반이다.
    """

    key: str
    label: str
    entry_sql: str
    scored_sql: str
    scoring_definition: str


TRACK_SAMPLE_SPECS: dict[str, SampleSpec] = {
    "crypto": SampleSpec(
        key="crypto",
        label="크립토 페이퍼",
        entry_sql="SELECT entry_bar_at AS t FROM paper_trades",
        scored_sql="SELECT exit_at AS t FROM paper_trades WHERE status='closed' AND exit_at IS NOT NULL",
        scoring_definition="청산 완료 거래 1건 = 표본 1",
    ),
    "stock_kr": SampleSpec(
        key="stock_kr",
        label="주식 페이퍼(KR)",
        entry_sql="SELECT filled_at AS t FROM stock_paper_fills WHERE market='KR' AND side='buy'",
        scored_sql="SELECT filled_at AS t FROM stock_paper_fills WHERE market='KR' AND side='sell'",
        scoring_definition="매도 체결 1건 = 표본 1 (진입-청산 쌍 완결)",
    ),
    "stock_us": SampleSpec(
        key="stock_us",
        label="주식 페이퍼(US)",
        entry_sql="SELECT filled_at AS t FROM stock_paper_fills WHERE market='US' AND side='buy'",
        scored_sql="SELECT filled_at AS t FROM stock_paper_fills WHERE market='US' AND side='sell'",
        scoring_definition="매도 체결 1건 = 표본 1 (진입-청산 쌍 완결)",
    ),
    "poly": SampleSpec(
        key="poly",
        label="폴리마켓 페이퍼",
        entry_sql="SELECT opened_at AS t FROM poly_positions",
        scored_sql="SELECT resolved_at AS t FROM poly_resolutions",
        scoring_definition="만기 정산 1건 = 표본 1 (Brier 채점 가능)",
    ),
}


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _timestamps(connection: sqlite3.Connection, sql: str) -> list[datetime]:
    """증거 쿼리 실행. 테이블이 아직 없으면 0건으로 본다 — 없는 것은 0이지 추정 대상이 아니다."""
    try:
        rows = connection.execute(sql).fetchall()
    except sqlite3.OperationalError:
        return []
    stamps = [_parse(row["t"] if isinstance(row, sqlite3.Row) else row[0]) for row in rows]
    return sorted([stamp for stamp in stamps if stamp is not None])


def poisson_rate_ci(count: int, exposure: float, *, z: float = 1.96) -> tuple[float, float]:
    """진입률(건/유효일)의 95% 신뢰구간 — Byar 근사.

    관측 창이 짧아 비율이 불안정하다는 사실을 숫자로 남기기 위해 붙인다(1-1). 외부 의존성
    없이 닫힌 형태로 계산한다. 관측 0건이면 하한은 0이고 상한만 의미가 있다.
    """
    if exposure <= 0:
        return (0.0, 0.0)
    if count <= 0:
        upper = ((1 - 1 / 9 + z / 3) ** 3) / exposure
        return (0.0, round(upper, 3))
    lower = count * (1 - 1 / (9 * count) - z / (3 * math.sqrt(count))) ** 3 / exposure
    upper = (count + 1) * (1 - 1 / (9 * (count + 1)) + z / (3 * math.sqrt(count + 1))) ** 3 / exposure
    return (round(max(0.0, lower), 3), round(upper, 3))


def _coverage_rows(connection: sqlite3.Connection, track: str) -> list[dict[str, Any]]:
    try:
        rows = connection.execute("SELECT * FROM observation_coverage WHERE track=? ORDER BY day", (track,)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(row) for row in rows]


def _valid_days(rows: list[dict[str, Any]]) -> set[str]:
    return {str(row["day"]) for row in rows if int(row.get("valid") or 0) == 1}


def track_sample_viability(
    connection: sqlite3.Connection,
    track: str,
    *,
    now: datetime | None = None,
    target_days: int = TARGET_EFFECTIVE_DAYS,
    target_samples: int = TARGET_SAMPLES,
) -> dict[str, Any]:
    """트랙 하나의 표본 생성 능력 판정 (PHASE 1-1·1-2).

    진입률의 분모는 **유효 관측일**이다. 엔진이 멈춰 있던 날을 분모에 넣으면 진입률이
    희석돼 "표본이 안 나온다"는 결론이 잘못 나온다 — 그건 선정 기준 문제가 아니라 정지 문제다.
    """
    now = now or datetime.now(timezone.utc)
    spec = TRACK_SAMPLE_SPECS[track]
    coverage = _coverage_rows(connection, track)
    clock = observation.verification_clock(coverage, target_days=target_days)
    valid_days = _valid_days(coverage)
    effective_days = len(valid_days)

    entries = _timestamps(connection, spec.entry_sql)
    scored = _timestamps(connection, spec.scored_sql)
    # 유효 관측일에 발생한 진입만 분자로 쓴다 — 분모(유효 관측일)와 같은 창이어야 비율이 성립한다.
    entries_in_window = [stamp for stamp in entries if stamp.date().isoformat() in valid_days]
    entries_total = len(entries)
    scored_total = len(scored)

    entries_per_effective_day = round(len(entries_in_window) / effective_days, 3) if effective_days else 0.0
    rate_ci = poisson_rate_ci(len(entries_in_window), effective_days)
    # 청산 완료율: 지금까지의 진입 중 채점까지 끝난 비율. 진행 중 포지션 때문에 아래로 편향된다
    # (우측 절단). 그래서 예상 표본은 **보수적**으로 나온다 — 그 방향이 안전하다.
    exit_completion_rate = round(scored_total / entries_total, 3) if entries_total else 0.0

    remaining_effective_days = max(0, target_days - effective_days)
    projected_entries = len(entries_in_window) + entries_per_effective_day * remaining_effective_days
    projected_samples = int(projected_entries * exit_completion_rate) if entries_total else 0

    structural = _structural_block(connection, track, now=now)
    verdict, reason, notes = _classify(
        track=track,
        effective_days=effective_days,
        entries_in_window=len(entries_in_window),
        scored_total=scored_total,
        projected_samples=projected_samples,
        target_samples=target_samples,
        exit_completion_rate=exit_completion_rate,
        structural=structural,
    )

    # N≥30 달성에 필요한 유효 관측일과, 실현 속도로 환산한 달력일.
    sample_per_effective_day = entries_per_effective_day * exit_completion_rate
    effective_days_to_target = (
        math.ceil((target_samples - scored_total) / sample_per_effective_day) if sample_per_effective_day > 0 and scored_total < target_samples else 0
    )
    realized_rate = float(clock.get("effective_rate") or 0.0)
    calendar_days_to_target = int(round(effective_days_to_target / realized_rate)) if effective_days_to_target and realized_rate > 0 else None

    return {
        "track": track,
        "label": spec.label,
        "scoring_definition": spec.scoring_definition,
        "effective_days": effective_days,
        "effective_days_target": target_days,
        "first_valid_day": clock.get("first_valid_day"),
        "realized_rate": realized_rate,
        "entries_total": entries_total,
        "entries_in_effective_days": len(entries_in_window),
        "entries_per_effective_day": entries_per_effective_day,
        "entries_per_effective_day_ci95": list(rate_ci),
        "scored_samples": scored_total,
        "scored_samples_target": target_samples,
        "exit_completion_rate": exit_completion_rate,
        "projected_samples_at_target": projected_samples,
        "reaches_target": projected_samples >= target_samples,
        "effective_days_to_target": effective_days_to_target,
        "calendar_days_to_target": calendar_days_to_target,
        "verdict": verdict,
        "verdict_reason": reason,
        "notes": notes,
        "structural_block": structural,
        # 규칙 2: N<30 이면 성적을 계산하지 않는다. 이 플래그가 화면·리포트의 근거다.
        "sample_sufficient": scored_total >= target_samples,
        "statement": _statement(spec.label, effective_days, target_days, scored_total, target_samples),
    }


def _classify(
    *,
    track: str,
    effective_days: int,
    entries_in_window: int,
    scored_total: int,
    projected_samples: int,
    target_samples: int,
    exit_completion_rate: float,
    structural: dict[str, Any] | None,
) -> tuple[str, str, list[str]]:
    """판정 (PHASE 1-2). 구조적 차단이 최우선 — 관측일을 더 쌓아도 해결되지 않기 때문이다."""
    notes: list[str] = []
    if structural and structural.get("blocked"):
        return VERDICT_STRUCTURALLY_BLOCKED, str(structural.get("reason")), [*notes, str(structural.get("detail") or "")]
    if effective_days < MIN_EFFECTIVE_DAYS_FOR_RATE:
        return (
            VERDICT_INSUFFICIENT_DATA,
            f"유효 관측일 {effective_days}일 < 판정 최소 {MIN_EFFECTIVE_DAYS_FOR_RATE}일 — 비율을 신뢰할 수 없다",
            [f"재판정 시점: 유효 관측일 {MIN_EFFECTIVE_DAYS_FOR_RATE}일 도달 시"],
        )
    if entries_in_window == 0:
        return (
            VERDICT_INSUFFICIENT_DATA,
            f"유효 관측일 {effective_days}일 동안 진입 0건 — 진입률을 산출할 수 없다",
            ["진입 0을 유니버스 크기로 추정하지 않는다. 선정 게이트 계측(PHASE 4)이 선행 과제다"],
        )
    if scored_total == 0 and exit_completion_rate == 0:
        notes.append("청산 완료 0건 — 청산 완료율을 실측할 수 없어 예상 표본이 0으로 나온다(추정치를 만들지 않는다)")
        return VERDICT_INSUFFICIENT_DATA, "채점 완료 표본 0건 — 청산 완료율 미실측", notes
    if projected_samples >= target_samples:
        return VERDICT_VIABLE, f"D+{TARGET_EFFECTIVE_DAYS} 도달 시 예상 표본 {projected_samples} ≥ {target_samples}", notes
    return (
        VERDICT_SLOW,
        f"D+{TARGET_EFFECTIVE_DAYS} 도달 시 예상 표본 {projected_samples} < {target_samples} — 검증 창 안에 N≥{target_samples} 미달",
        [*notes, "창 연장 또는 선정 기준 재검토가 필요하다. 목표 표본 수를 낮추는 것은 선택지가 아니다"],
    )


def _statement(label: str, effective_days: int, target_days: int, scored: int, target_samples: int) -> str:
    """진술 규칙(PHASE 6-4). 캘린더 일수로 진행을 말하지 않는다."""
    shortfall = "표본 부족" if scored < target_samples else "표본 충족"
    return f"{label} 유효 관측일 {effective_days}/{target_days}, 채점 가능 표본 {scored}/{target_samples} — {shortfall}"


# ── 구조적 차단 판정 ────────────────────────────────────────────────────


def _structural_block(connection: sqlite3.Connection, track: str, *, now: datetime) -> dict[str, Any] | None:
    """선정 기준상 표본이 **생성될 수 없는** 상태인지 본다.

    관측일을 더 쌓아도 해결되지 않는다는 점에서 SLOW 와 근본적으로 다르다. 폴리가 그 예다 —
    만기가 검증 종료 이후인 시장만 골라 두면 28일을 다 채워도 정산이 0이다.
    """
    if track != "poly":
        return None
    return poly_structural_block(connection, now=now)


def poly_structural_block(connection: sqlite3.Connection, *, now: datetime) -> dict[str, Any]:
    """폴리 구조적 차단 실측 (PHASE 1-2·2-1).

    가설을 세우고 실측으로 반증을 시도한다: "보유 포지션 중 검증 종료 전 만기가 하나라도
    있는가?" 하나도 없고, 유니버스에도 창 안 만기 후보가 없다면 그때만 차단이다.
    """
    deadline = _validation_deadline(connection)
    detail: dict[str, Any] = {"validation_ends_at": deadline.isoformat() if deadline else None}
    try:
        rows = connection.execute(
            """SELECT p.market_id, p.status, m.end_at FROM poly_positions p
            JOIN poly_markets m ON m.market_id=p.market_id WHERE p.status='open'"""
        ).fetchall()
    except sqlite3.OperationalError:
        return {"blocked": False, "reason": "poly_tables_absent", "detail": "폴리 테이블이 없다", **detail}
    open_positions = len(rows)
    ends = [_parse(row["end_at"]) for row in rows]
    within = [end for end in ends if end is not None and deadline is not None and end <= deadline]
    detail["open_positions"] = open_positions
    detail["settling_within_validation"] = len(within)
    detail["nearest_end_at"] = min([end for end in ends if end is not None]).isoformat() if any(ends) else None

    # 유니버스에 창 안 만기 후보가 실제로 존재하는가 — "데이터가 없다"와 "안 고른다"를 가른다.
    try:
        universe = connection.execute(
            "SELECT COUNT(*) AS n FROM poly_markets WHERE closed=0 AND end_at IS NOT NULL AND end_at > ? AND end_at <= ?",
            (now.isoformat(), deadline.isoformat() if deadline else now.isoformat()),
        ).fetchone()
        detail["universe_markets_within_window"] = int(universe["n"] if isinstance(universe, sqlite3.Row) else universe[0])
    except sqlite3.OperationalError:
        detail["universe_markets_within_window"] = 0

    if deadline is None:
        return {"blocked": False, "reason": "validation_deadline_unknown", "detail": "검증 종료일이 없어 판정 불가", **detail}
    if open_positions > 0 and not within:
        return {
            "blocked": True,
            "reason": f"보유 {open_positions}건 전부 검증 종료({deadline.date().isoformat()}) 이후 만기 — 검증 창 내 정산 예정 0건",
            "detail": (
                f"유니버스에는 창 안 만기 시장이 {detail['universe_markets_within_window']}개 있다. "
                "데이터 문제가 아니라 선정 기준에 만기 조건이 없다는 문제다(PHASE 2)."
            ),
            **detail,
        }
    return {"blocked": False, "reason": "검증 창 내 정산 예정 건 존재", "detail": f"{len(within)}건", **detail}


def _validation_deadline(connection: sqlite3.Connection) -> datetime | None:
    for sql in ("SELECT ends_at FROM poly_paper_track WHERE id=1", "SELECT MAX(ends_at) AS ends_at FROM stock_paper_tracks"):
        try:
            row = connection.execute(sql).fetchone()
        except sqlite3.OperationalError:
            continue
        if row is None:
            continue
        parsed = _parse(row["ends_at"] if isinstance(row, sqlite3.Row) else row[0])
        if parsed is not None:
            return parsed
    return None


# ── PHASE 6: 검증 완료 정의 ─────────────────────────────────────────────


def regime_coverage(connection: sqlite3.Connection, track: str) -> dict[str, Any]:
    """채점 가능 표본이 몇 개의 시장 국면을 포함하는지 (완료 조건 3).

    국면 라벨은 **진입 시점에 기록된 것만** 쓴다. 사후에 다시 라벨링하면 룩어헤드다.
    라벨이 없는 트랙은 `available=False` 로 두고 조건 미달로 남긴다 — 알 수 없는 것을
    충족으로 처리하면 완료 정의가 무의미해진다.
    """
    if track != "crypto":
        return {
            "available": False,
            "observed": [],
            "distinct": 0,
            "target": TARGET_REGIMES,
            "reason": "진입 시점 국면 라벨이 기록되지 않는 트랙 — 라벨 배선 전까지 미달로 둔다",
        }
    try:
        rows = connection.execute("SELECT payload FROM paper_trades WHERE status='closed'").fetchall()
    except sqlite3.OperationalError:
        rows = []
    labels: dict[str, int] = {}
    for row in rows:
        try:
            payload = json.loads(str(row["payload"] if isinstance(row, sqlite3.Row) else row[0]))
        except (ValueError, TypeError):
            continue
        evidence = payload.get("entry_evidence")
        regime = str((evidence or {}).get("market_regime") or "") if isinstance(evidence, dict) else ""
        if regime and regime != "unknown":
            labels[regime] = labels.get(regime, 0) + 1
    return {
        "available": True,
        "observed": sorted(labels),
        "counts": labels,
        "distinct": len(labels),
        "target": TARGET_REGIMES,
        "reason": None if len(labels) >= TARGET_REGIMES else "표본이 포함한 국면이 부족하다",
    }


def validation_completion(
    connection: sqlite3.Connection,
    track: str,
    *,
    now: datetime | None = None,
    target_days: int = TARGET_EFFECTIVE_DAYS,
    target_samples: int = TARGET_SAMPLES,
) -> dict[str, Any]:
    """트랙별 검증 완료 판정 (PHASE 6-2).

    ```
    검증 완료 = 유효 관측일 >= 28  AND  채점 가능 표본 >= 30  AND  국면 >= 2
    ```

    셋을 **모두** 만족해야 완료다. 하나라도 미달이면 미달 항목을 명시한다. 유효일만 크게
    표시하지 않는다 — **가장 뒤처진 조건이 그 트랙의 상태다.**
    """
    viability = track_sample_viability(connection, track, now=now, target_days=target_days, target_samples=target_samples)
    regimes = regime_coverage(connection, track)
    conditions = {
        "effective_days": {
            "label": "유효 관측일",
            "current": viability["effective_days"],
            "target": target_days,
            "met": viability["effective_days"] >= target_days,
        },
        "scored_samples": {
            "label": "채점 가능 표본",
            "current": viability["scored_samples"],
            "target": target_samples,
            "met": viability["scored_samples"] >= target_samples,
        },
        "regimes": {
            "label": "시장 국면",
            "current": regimes["distinct"],
            "target": TARGET_REGIMES,
            "met": bool(regimes.get("available")) and regimes["distinct"] >= TARGET_REGIMES,
        },
    }
    unmet = [value["label"] for value in conditions.values() if not value["met"]]
    # 진행률은 **가장 뒤처진 조건**으로 말한다. 평균을 내면 유효일이 표본 0을 가려버린다.
    laggard = min(conditions.items(), key=lambda item: _progress(item[1]))
    return {
        "track": track,
        "label": viability["label"],
        "complete": not unmet,
        "conditions": conditions,
        "unmet": unmet,
        "laggard": laggard[0],
        "verdict": viability["verdict"],
        "structural_block": viability["structural_block"],
        "regimes": regimes,
        "line": _completion_line(viability, regimes, conditions, unmet),
    }


def _progress(condition: dict[str, Any]) -> float:
    target = float(condition.get("target") or 0)
    return float(condition.get("current") or 0) / target if target else 1.0


def _completion_line(viability: dict[str, Any], regimes: dict[str, Any], conditions: dict[str, Any], unmet: list[str]) -> str:
    """대시보드 한 줄 (PHASE 6-3 형식)."""
    regime_cell = f"{regimes['distinct']}/{TARGET_REGIMES}" if regimes.get("available") else "-"
    base = (
        f"{viability['label']}  유효일 {conditions['effective_days']['current']}/{conditions['effective_days']['target']}"
        f"  ·  표본 {conditions['scored_samples']['current']}/{conditions['scored_samples']['target']}"
        f"  ·  국면 {regime_cell}"
    )
    if viability["verdict"] == VERDICT_STRUCTURALLY_BLOCKED:
        return f"{base}     [구조적 차단]"
    return f"{base}     [미달: {', '.join(unmet)}]" if unmet else f"{base}     [완료]"


def sample_viability_report(
    connection: sqlite3.Connection,
    *,
    now: datetime | None = None,
    target_days: int = TARGET_EFFECTIVE_DAYS,
    target_samples: int = TARGET_SAMPLES,
) -> dict[str, Any]:
    """4트랙 전체 판정 — PHASE 1 산출물의 기계 판독본."""
    now = now or datetime.now(timezone.utc)
    tracks = {track: track_sample_viability(connection, track, now=now, target_days=target_days, target_samples=target_samples) for track in TRACK_SAMPLE_SPECS}
    completion = {
        track: validation_completion(connection, track, now=now, target_days=target_days, target_samples=target_samples) for track in TRACK_SAMPLE_SPECS
    }
    return {
        "as_of": now.isoformat(),
        "principle": "관측일은 검증의 필요조건이지 충분조건이 아니다 — 채점 가능한 표본이 나와야 검증이다.",
        "target_effective_days": target_days,
        "target_samples": target_samples,
        "target_regimes": TARGET_REGIMES,
        "tracks": tracks,
        "completion": completion,
        "verdicts": {track: row["verdict"] for track, row in tracks.items()},
        "blocked_tracks": [track for track, row in tracks.items() if row["verdict"] == VERDICT_STRUCTURALLY_BLOCKED],
    }


def observation_window(connection: sqlite3.Connection, track: str) -> tuple[date | None, date | None]:
    """유효 관측일의 첫날·마지막날. 리포트에서 창을 명시하기 위한 보조."""
    days = sorted(_valid_days(_coverage_rows(connection, track)))
    if not days:
        return (None, None)
    return (date.fromisoformat(days[0]), date.fromisoformat(days[-1]))


def days_since(day: date | None, *, now: datetime) -> int:
    return (now.date() - day).days if day else 0


__all__ = [
    "MIN_EFFECTIVE_DAYS_FOR_RATE",
    "TARGET_EFFECTIVE_DAYS",
    "TARGET_REGIMES",
    "TARGET_SAMPLES",
    "TRACK_SAMPLE_SPECS",
    "VERDICT_INSUFFICIENT_DATA",
    "VERDICT_SLOW",
    "VERDICT_STRUCTURALLY_BLOCKED",
    "VERDICT_VIABLE",
    "days_since",
    "observation_window",
    "poisson_rate_ci",
    "poly_structural_block",
    "regime_coverage",
    "sample_viability_report",
    "track_sample_viability",
    "validation_completion",
]
