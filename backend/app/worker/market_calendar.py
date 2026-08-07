"""WO-FCE-SAMPLE-VIABILITY-01 PHASE 3 — 거래일 달력.

## 왜 이 모듈이 생겼나

직전 WO는 휴장 달력을 보관하지 않았고, 그래서 **공휴일이 "관측 0건"으로 유실일에 섞였다**
(`docs/ObservationIntegrity.md` §"알려진 한계 — 공휴일"). KR 커버리지 54.2%를 두고
"수집기를 고쳐야 한다"고 결론내기 전에 **먼저 확인할 것**이 이것이다:

> 휴장일을 유실로 세고 있으면 고칠 것은 계산식이지 수집기가 아니다.

## 확정과 미확정을 가른다

휴장일을 잘못 넣으면 **진짜 유실일이 분모에서 빠져 검증 진행도가 부풀려진다.** 그 방향의
오류가 반대 방향보다 훨씬 위험하다. 그래서 두 단계로 나눈다.

| 구분 | 분모 영향 | 근거 |
| --- | --- | --- |
| `CONFIRMED_HOLIDAYS` | 비거래일 — 분모에서 뺀다 | 법정 고정일 + 확인된 휴장일 |
| `PENDING_HOLIDAYS` | **아무 영향 없다** — 유실일로 남는다 | 확인 필요. 사람이 확인하면 CONFIRMED 로 옮긴다 |

미확정은 조용히 넘어가지 않는다. 진단 표면이 "이 날은 휴장일 수 있다"고 올려서 사람이
확인하게 한다. 추정으로 분모를 바꾸는 것보다 숫자가 나쁜 채로 질문하는 쪽이 낫다.

달력이 없는 기간(`CALENDAR_FROM` ~ `CALENDAR_THROUGH` 밖)은 주말만 비거래일로 보고
`calendar_covered=False` 로 표시한다 — 모르는 구간을 아는 척하지 않는다.
"""

from __future__ import annotations

from datetime import date, time

# 달력이 유효한 구간. 밖은 주말 판정만 하고 "달력 미보유"로 표시한다.
CALENDAR_FROM = date(2026, 1, 1)
CALENDAR_THROUGH = date(2026, 12, 31)

# ── 한국거래소(KRX) ─────────────────────────────────────────────────────
# 법정 고정일과 KRX 고유 휴장일(근로자의날·연말 폐장)만 확정으로 둔다.
KR_CONFIRMED: frozenset[date] = frozenset(
    {
        date(2026, 1, 1),  # 신정
        date(2026, 3, 1),  # 삼일절 (일요일)
        date(2026, 5, 1),  # 근로자의 날 — KRX 휴장
        date(2026, 5, 5),  # 어린이날
        date(2026, 6, 6),  # 현충일 (토요일)
        # 제헌절 — 2008년 공휴일에서 빠졌다가 2026년 재지정(2026-04-28 국무회의 의결).
        # KRX 주식·ETF·ETN·ELW·파생·일반상품 시장 전부 휴장. 2026-07-17은 금요일이라
        # 대체공휴일이 붙지 않는다. **검증 창 안에 있는 날이라 분모에 직접 영향을 준다.**
        date(2026, 7, 17),
        date(2026, 8, 15),  # 광복절 (토요일)
        date(2026, 10, 3),  # 개천절 (토요일)
        date(2026, 10, 9),  # 한글날
        date(2026, 12, 25),  # 성탄절
        date(2026, 12, 31),  # KRX 연말 휴장일
    }
)

# 확인이 필요한 날 — **분모를 바꾸지 않는다.** 음력 연휴·대체공휴일·선거일처럼 매년
# 관보로 확정되는 날들이다. 확인 전까지 유실일로 남겨 두고 진단에 올린다.
KR_PENDING: dict[date, str] = {
    date(2026, 2, 16): "설 연휴(확인 필요)",
    date(2026, 2, 17): "설날(확인 필요)",
    date(2026, 2, 18): "설 연휴(확인 필요)",
    date(2026, 3, 2): "삼일절 대체공휴일(확인 필요)",
    date(2026, 5, 25): "부처님오신날 대체공휴일(확인 필요)",
    date(2026, 6, 3): "전국동시지방선거일(확인 필요)",
    date(2026, 8, 17): "광복절 대체공휴일(확인 필요)",
    date(2026, 9, 24): "추석 연휴(확인 필요)",
    date(2026, 9, 25): "추석(확인 필요)",
    date(2026, 10, 5): "개천절 대체공휴일(확인 필요)",
}

# ── 뉴욕증권거래소(NYSE) ────────────────────────────────────────────────
US_CONFIRMED: frozenset[date] = frozenset(
    {
        date(2026, 1, 1),  # New Year's Day
        date(2026, 1, 19),  # Martin Luther King Jr. Day
        date(2026, 2, 16),  # Washington's Birthday
        date(2026, 4, 3),  # Good Friday
        date(2026, 5, 25),  # Memorial Day
        date(2026, 6, 19),  # Juneteenth
        date(2026, 7, 3),  # Independence Day (관측일 — 7/4 토요일)
        date(2026, 9, 7),  # Labor Day
        date(2026, 11, 26),  # Thanksgiving
        date(2026, 12, 25),  # Christmas
    }
)
US_PENDING: dict[date, str] = {}

# 조기 폐장 — 세션 창을 줄인다. 정상 폐장 시각을 쓰면 닫힌 시간이 통째로 "공백"이 된다.
US_EARLY_CLOSES: dict[date, time] = {
    date(2026, 7, 2): time(13, 0),  # Independence Day 전일
    date(2026, 11, 27): time(13, 0),  # Thanksgiving 다음날
    date(2026, 12, 24): time(13, 0),  # Christmas Eve
}
# KR 은 수능일 개장 지연(10:00) 등이 있으나 날짜가 확정되지 않아 넣지 않는다.
KR_EARLY_CLOSES: dict[date, time] = {}

_CONFIRMED = {"KR": KR_CONFIRMED, "US": US_CONFIRMED}
_PENDING = {"KR": KR_PENDING, "US": US_PENDING}
_EARLY_CLOSES = {"KR": KR_EARLY_CLOSES, "US": US_EARLY_CLOSES}


def calendar_covered(day: date) -> bool:
    """이 날짜가 달력 보유 구간 안인가. 밖이면 주말 판정만 유효하다."""
    return CALENDAR_FROM <= day <= CALENDAR_THROUGH


def is_market_holiday(market: str, day: date) -> bool:
    """확정 휴장일인가. **미확정(PENDING)은 False** — 분모를 바꾸지 않는다."""
    return day in _CONFIRMED.get(market, frozenset())


def pending_holiday_reason(market: str, day: date) -> str | None:
    """확인이 필요한 휴장 후보. 사람이 확인해 CONFIRMED 로 옮기기 전까지 유실일로 남는다."""
    return _PENDING.get(market, {}).get(day)


def early_close(market: str, day: date) -> time | None:
    return _EARLY_CLOSES.get(market, {}).get(day)


def is_trading_day(market: str, day: date) -> bool:
    """주말·확정 휴장일이 아니면 거래일."""
    return day.weekday() < 5 and not is_market_holiday(market, day)


def holiday_audit(market: str, days: list[date]) -> dict[str, object]:
    """유실일 목록에 휴장 후보가 섞여 있는지 감사 (PHASE 3-2 첫 항목).

    커버리지 저하 원인을 수집기에서 찾기 전에 이걸 먼저 본다. 미확정 후보가 유실일에
    섞여 있으면 그 숫자는 실제보다 나쁘게 나온 것이고, 고칠 대상은 계산식이다.
    """
    confirmed = [day for day in days if is_market_holiday(market, day)]
    pending = [(day, pending_holiday_reason(market, day)) for day in days if pending_holiday_reason(market, day)]
    uncovered = [day for day in days if not calendar_covered(day)]
    return {
        "market": market,
        "days_checked": len(days),
        # 확정 휴장일이 유실일 목록에 남아 있으면 계산 경로에 달력이 안 걸린 것이다.
        "confirmed_holidays_in_list": [day.isoformat() for day in confirmed],
        "pending_holiday_candidates": [{"day": day.isoformat(), "reason": reason} for day, reason in pending],
        "days_outside_calendar": [day.isoformat() for day in uncovered],
        "calendar_range": [CALENDAR_FROM.isoformat(), CALENDAR_THROUGH.isoformat()],
        "verdict": (
            "휴장 오계상 있음 — 계산식을 고쳐야 한다"
            if confirmed
            else "확인 필요 — 미확정 휴장 후보가 유실일에 섞여 있다"
            if pending
            else "휴장 오계상 없음 — 유실 원인은 다른 축에 있다"
        ),
    }


__all__ = [
    "CALENDAR_FROM",
    "CALENDAR_THROUGH",
    "calendar_covered",
    "early_close",
    "holiday_audit",
    "is_market_holiday",
    "is_trading_day",
    "pending_holiday_reason",
]
