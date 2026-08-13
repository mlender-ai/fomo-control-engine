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

from app.worker import market_calendar

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

    WO-FCE-SAMPLE-VIABILITY-01 PHASE 3: 주말뿐 아니라 **확정 휴장일**도 분모에서 뺀다.
    휴장일을 유실로 세면 커버리지가 실제보다 나쁘게 나오고, 그러면 멀쩡한 수집기를 고치게 된다.
    미확정 휴장 후보는 여기서 빼지 않는다(`market_calendar` 참조) — 추정으로 분모를 바꾸면
    진짜 유실일이 사라져 검증 진행도가 부풀려진다.
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
        if market_calendar.is_market_holiday(spec.market, local_day.date()):
            return None
        close_at = market_calendar.early_close(spec.market, local_day.date()) or close_at
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


def _non_trading_reason(spec: TrackSpec, day: date) -> str:
    """분모에서 빠진 이유를 구분해 남긴다 — 주말과 휴장을 같은 말로 적으면 감사할 수 없다."""
    if spec.market is not None and market_calendar.is_market_holiday(spec.market, day):
        return "비거래일(휴장)"
    return "비거래일(주말) 또는 세션 미개시"


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
            "reason": _non_trading_reason(spec, day),
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
        # 확정 휴장일은 위에서 분모에서 빠졌다. 여기 남는 것은 정지이거나 **미확정 휴장 후보**다.
        pending = market_calendar.pending_holiday_reason(spec.market, day) if spec.market else None
        reason = f"관측 0건 (정지 또는 휴장 확인 필요: {pending})" if pending else "관측 0건 (정지)"
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


def verification_clock(rows: list[dict[str, Any]], *, target_days: int = VALIDATION_TARGET_DAYS, anchor_day: str | None = None) -> dict[str, Any]:
    """검증 시계를 **유효 관측일 기준**으로 계산한다(1-4).

    숫자가 나빠져도 정직한 재계산이 우선이다. "D+21인데 유효 6일"이 사실이면 그대로 낸다.
    시계는 **첫 유효일부터** 시작한다 — 그 전은 관측 자체가 성립하지 않았다.

    WO-FCE-WINDOW-ANCHOR-01: `anchor_day` 가 주어지면 그 날 이후만 센다. 즉 "최초 유효일"이
    아니라 **"앵커 이후 최초 유효일"** 부터 시작한다. 앵커가 `None` 이면 현행과 동일하다(C7).
    """
    if anchor_day is not None:
        rows = [row for row in rows if str(row["day"]) >= str(anchor_day)]
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


# ── PHASE 3: 커버리지 저하 원인 축 분해 ─────────────────────────────────
#
# 직전 WO 교훈: "US 45시간을 절전 탓으로 돌리면 오귀인"이었다. 같은 함정을 피하려면
# **축을 먼저 분리**해야 한다. 원인을 모른 채 수집 주기를 올리는 것은 추측이지 수리가 아니다.


def _host_sleep_overlap_seconds(start: datetime, end: datetime) -> int:
    """세션 창이 절전 구간(17:00~20:00 UTC)과 **구조적으로** 겹치는 초.

    이 값이 0이면 그 트랙의 유실은 절전으로 설명될 수 없다 — 실측을 볼 것도 없이 그렇다.
    KR 정규장(00:00~06:30 UTC)이 그 예다.
    """
    total = 0
    cursor = start.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    for _ in range(3):
        window_start = datetime.combine(cursor.date(), LATE_SESSION_UTC[0], tzinfo=timezone.utc)
        window_end = datetime.combine(cursor.date(), LATE_SESSION_UTC[1], tzinfo=timezone.utc)
        overlap = (min(end, window_end) - max(start, window_start)).total_seconds()
        if overlap > 0:
            total += int(overlap)
        cursor += timedelta(days=1)
    return total


def gap_axes(connection: sqlite3.Connection, track: str, day: date, *, now: datetime | None = None) -> dict[str, Any]:
    """하루치 커버리지 저하를 원인 축으로 쪼갠다 (PHASE 3-2).

    | 축 | 판별 |
    | --- | --- |
    | 휴장일 | 확정 휴장은 분모에서 빠짐 · 미확정 후보는 여기서 표시 |
    | 호스트 절전 | 세션 창이 17:00~20:00 UTC 와 겹치는가 (겹치지 않으면 절전 설명 불가) |
    | 장 시간 경계 | 공백이 세션 앞/뒤에 몰리는가 (롤오버 유사 결함의 지문) |
    | 소스 응답 | 같은 시각 24/7 트랙은 살아 있었는가 (수집기 문제 vs 호스트 문제) |
    """
    now = now or datetime.now(timezone.utc)
    spec = TRACK_SPECS[track]
    bounds = session_bounds(track, day, now=now)
    pending = market_calendar.pending_holiday_reason(spec.market, day) if spec.market else None
    if bounds is None:
        return {
            "day": day.isoformat(),
            "track": track,
            "trading_day": 0,
            "axes": {"holiday": {"confirmed": bool(spec.market and market_calendar.is_market_holiday(spec.market, day)), "pending": pending}},
        }
    start, end = bounds
    stamps = _observation_times(connection, spec, start, end)
    leading = int(((stamps[0] if stamps else end) - start).total_seconds())
    trailing = int((end - (stamps[-1] if stamps else start)).total_seconds())
    interior = 0
    cursor = stamps[0] if stamps else start
    for stamp in stamps[1:]:
        gap = int((stamp - cursor).total_seconds())
        if gap > BIN_SECONDS:
            interior += gap
        cursor = stamp
    # 같은 벽시계 창에서 24/7 트랙이 살아 있었는지 — 호스트 정지와 트랙 고유 결함을 가른다.
    host_alive_stamps = _observation_times(connection, TRACK_SPECS["crypto"], start, end)
    span = max(1, int((end - start).total_seconds()))
    host_bins = max(1, -(-span // BIN_SECONDS))
    host_covered = len({int((stamp - start).total_seconds()) // BIN_SECONDS for stamp in host_alive_stamps})
    host_coverage_pct = round(min(host_covered, host_bins) / host_bins * 100, 2)
    structural_sleep_overlap = _host_sleep_overlap_seconds(start, end)
    return {
        "day": day.isoformat(),
        "track": track,
        "trading_day": 1,
        "observations": len(stamps),
        "session_seconds": span,
        "axes": {
            "holiday": {
                "confirmed": False,
                "pending": pending,
                # 미확정 후보인데 그날 24/7 트랙은 정상이었다면 "우리가 죽은" 게 아니라
                # "시장이 닫혔을" 가능성이 크다. 자동 반영하지 않고 사람 확인용으로 올린다.
                "suspected_closure": bool(pending and not stamps and host_coverage_pct >= MIN_COVERAGE_PCT),
            },
            "host_sleep": {
                "session_overlaps_sleep_window": structural_sleep_overlap > 0,
                "structural_overlap_seconds": structural_sleep_overlap,
                "explanation_possible": structural_sleep_overlap > 0,
            },
            "session_edge": {
                "leading_gap_seconds": leading if leading > BIN_SECONDS else 0,
                "trailing_gap_seconds": trailing if trailing > BIN_SECONDS else 0,
                "interior_gap_seconds": interior,
                # 앞·뒤 공백이 내부 공백보다 크면 세션 경계 결함(롤오버 계열)의 지문이다.
                # 관측이 0건이면 세션 전체가 공백이라 이 판별이 성립하지 않는다 — 그건 경계
                # 결함이 아니라 종일 정지다. 오귀인을 막기 위해 관측이 있는 날만 본다.
                "edge_dominant": bool(stamps) and (leading + trailing) > max(interior, BIN_SECONDS),
            },
            "source_response": {
                "host_247_coverage_pct": host_coverage_pct,
                "host_alive": host_coverage_pct >= MIN_COVERAGE_PCT,
                # 호스트는 살아 있는데 이 트랙만 비었다 = 수집기·소스 문제다.
                "track_specific_failure": bool(host_coverage_pct >= MIN_COVERAGE_PCT and len(stamps) == 0),
            },
        },
    }


def axis_breakdown(connection: sqlite3.Connection, track: str, days: list[date], *, now: datetime | None = None) -> dict[str, Any]:
    """기간 축 분해 요약 — "무엇을 고쳐야 하는가"의 근거 (PHASE 3-2).

    축이 분리되지 않은 채로는 어떤 수리도 추측이다. 이 표가 나오기 전에 수집 주기를 올리지 않는다.
    """
    now = now or datetime.now(timezone.utc)
    rows = [gap_axes(connection, track, day, now=now) for day in days]
    trading = [row for row in rows if row["trading_day"] == 1]
    holiday_rows = [row for row in rows if row["trading_day"] == 0]
    suspected = [row["day"] for row in trading if row["axes"]["holiday"]["suspected_closure"]]
    pending_days = [row["day"] for row in rows if row["axes"]["holiday"].get("pending")]
    sleep_capable = [row for row in trading if row["axes"]["host_sleep"]["explanation_possible"]]
    edge_dominant = [row["day"] for row in trading if row["axes"]["session_edge"]["edge_dominant"]]
    track_specific = [row["day"] for row in trading if row["axes"]["source_response"]["track_specific_failure"]]
    return {
        "track": track,
        "days_examined": len(rows),
        "non_trading_days": len(holiday_rows),
        "trading_days": len(trading),
        "holiday": {
            "confirmed_excluded": len([row for row in holiday_rows if row["axes"]["holiday"]["confirmed"]]),
            "pending_candidates": pending_days,
            "suspected_closure_days": suspected,
            "verdict": (
                f"휴장 확인 필요 {len(suspected)}일 — 수집기는 살아 있었고 이 트랙만 비었다. 확정되면 유실일이 아니다"
                if suspected
                else f"미확정 휴장 후보 {len(pending_days)}일 — 사람 확인 필요(호스트 생존 근거는 없음)"
                if pending_days
                else "유실일에 휴장 후보 없음 — 원인은 다른 축이다"
            ),
        },
        "host_sleep": {
            "days_where_sleep_can_explain": len(sleep_capable),
            # 0이면 이 트랙의 유실을 절전으로 설명하는 것은 오귀인이다. 구조적으로 겹치지 않는다.
            "verdict": (
                "세션 창이 절전 구간과 겹친다 — 절전이 원인일 수 있다"
                if sleep_capable
                else "세션 창이 절전 구간(17:00~20:00 UTC)과 겹치지 않는다 — 절전으로 설명 불가"
            ),
        },
        "session_edge": {"edge_dominant_days": edge_dominant, "verdict": ("세션 경계 결함 의심(롤오버 계열)" if edge_dominant else "경계 편중 없음")},
        "source_response": {
            "track_specific_failure_days": track_specific,
            "verdict": ("호스트는 살아 있는데 이 트랙만 비었다 — 수집기·소스 축" if track_specific else "트랙 고유 실패일 없음"),
        },
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
