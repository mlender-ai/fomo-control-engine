"""WO-FCE-OBSERVATION-INTEGRITY-01 Phase 1 — 관측 무결성 게이트.

## 왜 이 모듈이 생겼나

지난 2주간 발견된 관측 손실:

| 사건 | 손실 |
| --- | --- |
| 워커 매달림 | 10.4시간 · 11.7시간 |
| 토스 인증 자기잠금 | 20.8시간 |
| KST 자정 롤오버 | 매일 US 정규장의 77% |
| KOSPI100 유실 | 12일 |
| 맥 절전 | `heartbeat_stale` 재시작 5회 |

매번 새 결함을 찾아 고쳤지만 **"결함이 없는 날이 며칠 쌓였는가"를 세는 장치가 없었다.**
그래서 "표본이 오염됐으니 다시 세자"가 반복됐다.

> **원칙: 검증일은 커버리지 게이트를 통과한 날만 센다.**
> 통과 못 한 날은 유실일이며, 검증 시계는 **첫 통과일부터** 센다.

## 어떻게 재나

세션 창을 15분 구간(bin)으로 쪼개고, 관측 흔적이 하나라도 있는 구간의 비율을 커버리지로 본다.
관측 주기가 불규칙해도(10초~15분) 흔들리지 않고, "장 시작 90분만 수집"같은 부분 정지를 정확히
잡아낸다 — 실측 2026-08-04 US 는 13:30~14:57 만 수집해 커버리지 23%였다.

임계 90%는 보수적으로 잡았다. 근거: 정상 운영일의 실측 커버리지는 100%에 붙고(관측 주기 10초 ≪
bin 15분), 90% 미달은 세션의 39분 이상이 통째로 비었다는 뜻이라 표본 신뢰성이 실제로 흔들린다.
"거의 다 봤다"와 "온전히 봤다"를 구분하지 않으면 이 게이트를 만든 의미가 없다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

# 커버리지 판정 구간. 관측 주기(10초~15분)보다 크되 세션(6.5시간)보다 충분히 잘게.
BIN_SECONDS = 900
# 유효 관측일 임계(%). 미달일은 유실일.
MIN_COVERAGE_PCT = 90.0
# 4주 검증 목표일.
VALIDATION_TARGET_DAYS = 28
# 맥 절전이 주로 먹는 미국 정규장 후반(02:00~05:00 KST = 17:00~20:00 UTC).
LATE_SESSION_UTC = (time(17, 0), time(20, 0))


@dataclass(frozen=True)
class TrackSpec:
    key: str
    label: str
    table: str
    column: str
    market: str | None  # None = 24/7
    where: str = ""


# 트랙별 "실제로 관측했다"의 증거 테이블.
#
# 잡 하트비트가 아니라 **산출물**을 근거로 삼는다 — 잡은 조기 반환해도 "성공"으로 기록되기
# 때문이다(2026-07-28 사고: runs 8,076 · 오류 0 인데 수집 0건).
# 증거 테이블은 반드시 **append-only** 여야 한다. upsert 테이블은 시계열이 아니다 —
# `poly_markets` 는 PK 가 market_id 라 `observed_at` 이 "마지막으로 본 시각"만 남고, 이걸 근거로
# 쓰면 과거 커버리지가 통째로 0으로 보인다(초기 구현에서 실제로 폴리가 0일로 나왔다).
TRACK_SPECS: dict[str, TrackSpec] = {
    "crypto": TrackSpec("crypto", "크립토 페이퍼", "market_snapshots", "created_at", None),
    "stock_kr": TrackSpec("stock_kr", "주식 수집(KR)", "toss_quotes", "observed_at", "KR", "market='KR'"),
    "stock_us": TrackSpec("stock_us", "주식 수집(US)", "toss_quotes", "observed_at", "US", "market='US'"),
    "poly": TrackSpec("poly", "폴리마켓 페이퍼", "poly_estimates", "observed_at", None),
}

_SESSIONS = {
    "KR": (ZoneInfo("Asia/Seoul"), time(9, 0), time(15, 30)),
    "US": (ZoneInfo("America/New_York"), time(9, 30), time(16, 0)),
}


def session_bounds(track: str, day: date, *, now: datetime | None = None) -> tuple[datetime, datetime] | None:
    """그날 그 트랙이 관측했어야 하는 UTC 창. 비거래일이면 None(분모에서 제외).

    24/7 트랙은 하루 전체. 오늘은 아직 안 지난 시간까지 요구하면 항상 미달이므로 `now` 로 자른다.
    """
    now = now or datetime.now(timezone.utc)
    spec = TRACK_SPECS[track]
    if spec.market is None:
        start = datetime.combine(day, time(0, 0), tzinfo=timezone.utc)
        end = start + timedelta(days=1)
    else:
        zone, open_at, close_at = _SESSIONS[spec.market]
        local_day = datetime.combine(day, time(12, 0), tzinfo=zone)
        if local_day.weekday() >= 5:
            return None
        start = datetime.combine(local_day.date(), open_at, tzinfo=zone).astimezone(timezone.utc)
        end = datetime.combine(local_day.date(), close_at, tzinfo=zone).astimezone(timezone.utc)
    if start >= now:
        return None  # 아직 시작도 안 한 세션은 유실이 아니다
    return start, min(end, now)


def _observation_times(connection: sqlite3.Connection, spec: TrackSpec, start: datetime, end: datetime) -> list[datetime]:
    # 테이블·컬럼명은 SQL 파라미터로 바인딩할 수 없어 문자열로 조립한다.
    # 값은 전부 바인딩하고, 식별자는 오직 모듈 상수 TRACK_SPECS 에서만 온다(외부 입력 경로 없음).
    if spec.key not in TRACK_SPECS or TRACK_SPECS[spec.key] is not spec:
        raise ValueError(f"등록되지 않은 트랙 스펙: {spec.key}")
    where = f" AND {spec.where}" if spec.where else ""
    rows = connection.execute(
        f"SELECT {spec.column} AS t FROM {spec.table} WHERE {spec.column} >= ? AND {spec.column} < ?{where} ORDER BY t",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    stamps: list[datetime] = []
    for row in rows:
        raw = str(row["t"] if isinstance(row, sqlite3.Row) else row[0])
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        stamps.append(parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc))
    return stamps


def _late_session_overlap(gap_start: datetime, gap_end: datetime) -> int:
    """공백이 미국 정규장 후반(17:00~20:00 UTC)과 겹친 초. 맥 절전 손실 정량화용(1-3)."""
    day_start = gap_start.replace(hour=0, minute=0, second=0, microsecond=0)
    total = 0
    for offset in (-1, 0, 1):
        base = day_start + timedelta(days=offset)
        window_start = datetime.combine(base.date(), LATE_SESSION_UTC[0], tzinfo=timezone.utc)
        window_end = datetime.combine(base.date(), LATE_SESSION_UTC[1], tzinfo=timezone.utc)
        overlap = (min(gap_end, window_end) - max(gap_start, window_start)).total_seconds()
        if overlap > 0:
            total += int(overlap)
    return total


def daily_coverage(connection: sqlite3.Connection, track: str, day: date, *, now: datetime | None = None) -> dict[str, Any]:
    """하루치 커버리지. 비거래일은 `trading_day=0` 으로 분모에서 빠진다."""
    now = now or datetime.now(timezone.utc)
    spec = TRACK_SPECS[track]
    bounds = session_bounds(track, day, now=now)
    computed_at = now.isoformat()
    if bounds is None:
        return {
            "day": day.isoformat(),
            "track": track,
            "session_start_at": None,
            "session_end_at": None,
            "expected_bins": 0,
            "covered_bins": 0,
            "coverage_pct": 0.0,
            "observations": 0,
            "longest_gap_seconds": 0,
            "total_gap_seconds": 0,
            "late_session_gap_seconds": 0,
            "valid": 0,
            "trading_day": 0,
            "reason": "비거래일(주말) 또는 세션 미개시",
            "computed_at": computed_at,
        }

    start, end = bounds
    stamps = _observation_times(connection, spec, start, end)
    span = max(1, int((end - start).total_seconds()))
    expected_bins = max(1, -(-span // BIN_SECONDS))  # 올림
    covered = {int((stamp - start).total_seconds()) // BIN_SECONDS for stamp in stamps}
    covered_bins = len({index for index in covered if 0 <= index < expected_bins})
    coverage_pct = round(covered_bins / expected_bins * 100, 2)

    # 공백: 세션 시작 → 첫 관측 → … → 마지막 관측 → 세션 끝. 한 bin 을 넘는 간격만 공백으로 본다.
    longest_gap = 0
    total_gap = 0
    late_gap = 0
    cursor = start
    for stamp in [*stamps, end]:
        gap = int((stamp - cursor).total_seconds())
        if gap > BIN_SECONDS:
            longest_gap = max(longest_gap, gap)
            total_gap += gap
            late_gap += _late_session_overlap(cursor, stamp)
        cursor = max(cursor, stamp)

    valid = coverage_pct >= MIN_COVERAGE_PCT
    if valid:
        reason = None
    elif not stamps:
        # 휴장 달력을 보관하지 않으므로 공휴일도 여기 걸린다 — 소급 리포트에서 사람이 식별한다.
        reason = "관측 0건 (정지 또는 미확인 휴장)"
    else:
        reason = f"커버리지 {coverage_pct}% < 임계 {MIN_COVERAGE_PCT}% · 최장 공백 {longest_gap // 60}분"

    return {
        "day": day.isoformat(),
        "track": track,
        "session_start_at": start.isoformat(),
        "session_end_at": end.isoformat(),
        "expected_bins": expected_bins,
        "covered_bins": covered_bins,
        "coverage_pct": coverage_pct,
        "observations": len(stamps),
        "longest_gap_seconds": longest_gap,
        "total_gap_seconds": total_gap,
        "late_session_gap_seconds": late_gap,
        "valid": 1 if valid else 0,
        "trading_day": 1,
        "reason": reason,
        "computed_at": computed_at,
    }


def compute_range(
    connection: sqlite3.Connection,
    start_day: date,
    end_day: date,
    *,
    tracks: list[str] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """기간 소급 산출. 저장된 관측 이력만 쓰므로 과거도 그대로 다시 잰다."""
    now = now or datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for track in tracks or list(TRACK_SPECS):
        cursor = start_day
        while cursor <= end_day:
            rows.append(daily_coverage(connection, track, cursor, now=now))
            cursor += timedelta(days=1)
    return rows


def save_coverage(connection: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    connection.executemany(
        """INSERT INTO observation_coverage
        (day, track, session_start_at, session_end_at, expected_bins, covered_bins, coverage_pct,
         observations, longest_gap_seconds, total_gap_seconds, late_session_gap_seconds,
         valid, trading_day, reason, computed_at)
        VALUES (:day, :track, :session_start_at, :session_end_at, :expected_bins, :covered_bins, :coverage_pct,
                :observations, :longest_gap_seconds, :total_gap_seconds, :late_session_gap_seconds,
                :valid, :trading_day, :reason, :computed_at)
        ON CONFLICT(day, track) DO UPDATE SET
            session_start_at=excluded.session_start_at, session_end_at=excluded.session_end_at,
            expected_bins=excluded.expected_bins, covered_bins=excluded.covered_bins,
            coverage_pct=excluded.coverage_pct, observations=excluded.observations,
            longest_gap_seconds=excluded.longest_gap_seconds, total_gap_seconds=excluded.total_gap_seconds,
            late_session_gap_seconds=excluded.late_session_gap_seconds,
            valid=excluded.valid, trading_day=excluded.trading_day,
            reason=excluded.reason, computed_at=excluded.computed_at""",
        rows,
    )
    return len(rows)


def verification_clock(rows: list[dict[str, Any]], *, target_days: int = VALIDATION_TARGET_DAYS) -> dict[str, Any]:
    """검증 시계를 **유효 관측일 기준**으로 계산한다(1-4).

    숫자가 나빠져도 정직한 재계산이 우선이다. "D+21인데 유효 6일"이 사실이면 그대로 낸다.
    시계는 **첫 유효일부터** 시작한다 — 그 전은 관측 자체가 성립하지 않았다.
    """
    ordered = sorted(rows, key=lambda row: str(row["day"]))
    trading = [row for row in ordered if int(row.get("trading_day") or 0) == 1]
    valid_days = [row for row in trading if int(row.get("valid") or 0) == 1]
    first_valid = valid_days[0]["day"] if valid_days else None
    since_first = [row for row in trading if first_valid is not None and str(row["day"]) >= str(first_valid)]
    lost = [row for row in since_first if int(row.get("valid") or 0) != 1]
    effective = len(valid_days)
    coverages = [float(row.get("coverage_pct") or 0) for row in since_first]
    average = round(sum(coverages) / len(coverages), 1) if coverages else 0.0
    # 실현 속도로만 외삽한다 — 달력일 기준으로 추정하면 또 거짓말이 된다.
    rate = effective / len(since_first) if since_first else 0.0
    remaining = max(0, target_days - effective)
    projected = int(round(remaining / rate)) if rate > 0 else None
    return {
        "target_days": target_days,
        "first_valid_day": first_valid,
        "effective_days": effective,
        "lost_days": len(lost),
        "trading_days_since_start": len(since_first),
        "average_coverage_pct": average,
        "effective_rate": round(rate, 3),
        "remaining_effective_days": remaining,
        "projected_calendar_days_remaining": projected,
        "label": (
            f"검증 D+{effective}/{target_days} (유효 관측일 기준) · 유실 {len(lost)}일 · 커버리지 평균 {average}%"
            if first_valid
            else f"검증 미개시 — 유효 관측일 0일 (커버리지 {MIN_COVERAGE_PCT}% 이상인 날이 아직 없음)"
        ),
        "lost_day_details": [{"day": row["day"], "coverage_pct": row["coverage_pct"], "reason": row["reason"]} for row in lost],
    }


def manual_action_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """코드로 못 고치는 손실 — 사용자 조치가 필요한 항목(1-3).

    맥 절전은 코드가 해결할 수 없다. 조용히 유실로만 세면 영원히 안 고쳐지므로
    "수동 조치 필요"로 올려 상시 노출한다.
    """
    # 같은 벽시계 공백을 4트랙에서 각각 더하면 4배로 부풀려진다(C3 정직성).
    # 호스트 정지는 24/7 트랙 하나(crypto)로 재고, US 표본 영향은 stock_us 로만 따로 낸다.
    host_rows = [row for row in rows if row["track"] == "crypto"]
    us_rows = [row for row in rows if row["track"] == "stock_us"]
    host_total = sum(int(row.get("late_session_gap_seconds") or 0) for row in host_rows)
    us_total = sum(int(row.get("late_session_gap_seconds") or 0) for row in us_rows)
    if host_total <= 0 and us_total <= 0:
        return []
    affected = sorted({str(row["day"]) for row in host_rows if int(row.get("late_session_gap_seconds") or 0) > 0})
    return [
        {
            "kind": "host_sleep",
            "severity": "action_required",
            "title": "호스트(맥) 절전으로 관측 유실",
            # 귀인을 섞지 않는다: 24/7 트랙의 공백만이 호스트 정지의 순수한 증거다.
            # US 후반 공백은 KST 롤오버 결함(08-05 수리)이 대부분이었으므로 절전 탓으로 돌리면 거짓이다.
            "detail": (
                f"02:00~05:00 KST 구간 호스트 정지 {host_total // 3600}시간 {host_total % 3600 // 60}분 "
                f"({len(affected)}일, 24/7 트랙 기준). 같은 창의 US 정규장 총 유실은 "
                f"{us_total // 3600}시간이지만 그 대부분은 KST 롤오버 결함(2026-08-05 수리)이며, "
                f"절전분은 위 호스트 정지 시간까지다."
            ),
            "remedy": "전원 연결 유지 + `caffeinate -dimsu` 상시 실행 또는 시스템 설정에서 절전 해제. 코드로 해결 불가.",
            "affected_days": affected,
            "lost_seconds": host_total,
            "us_window_total_lost_seconds": us_total,
        }
    ]
