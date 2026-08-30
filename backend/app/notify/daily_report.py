"""WO-FCE-DAILY-REPORT-01 — 5트랙 일일 계좌 리포트.

## 지금 없는 것

텔레그램에 오는 것은 건별 사건뿐이다 — `엔진 진입 · HYPEUSDT 롱` · `엔진 청산 · net +4.33%`.
하루가 끝났을 때 **"지금 계좌가 얼마인가"** 에 답하는 메시지가 하나도 없다.

`TRACK-CAPITAL-01` 이 화면에는 그 숫자를 만들었다. 텔레그램에는 안 온다.

> 하루 1회, **그 메시지 하나만 읽어도 5트랙 상태를 전부 알 수 있어야 한다.**

## 계산하지 않는다 — 조립한다

이 모듈은 지표를 새로 만들지 않는다. 이미 있는 산출기를 읽어 문장으로 바꾼다:

| 값 | 출처 |
| --- | --- |
| 자본·실현·미실현 | `validation.track_capital` |
| 승률·PF·MDD | `paper.service._metric_payload` (스코어보드 경유) |
| 트랙 상태(정지·차단·보류) | `poly_paper.track_status` · `stock_paper.store` halt |
| 조치 필요 | `validation.pending_decisions` · `provisional_defaults` |
| 고래 추종 대상 | `paper.whale_follow.performance_by_whale` |

중복 구현이 곧 두 개의 진실이다. `METRIC-TRUTH-01` 이 그것으로 한 번 깨졌다 —
술어 차이만으로 사용자 성적이 +36.18%/PF 1.87 ↔ +17.57%/PF 0.55 로 뒤바뀌었다.

## 하지 않는 것

- **트랙 총합 줄을 만들지 않는다**(C5). 통화가 다르고 판정이 독립이다.
- **실현과 미실현을 합치지 않는다**(C4).
- **막힌 트랙을 정상처럼 쓰지 않는다**(C7). 상태 줄로 대체하고 성적은 표본 0 이다.
- **인과를 단정하지 않는다**(C9). 관측 서술만이다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

# 트랙 표시 순서와 라벨. 5트랙이 **항상 전부** 등장한다 — 이벤트가 없어서 침묵하던
# 구조를 `PERFORMANCE-REPORT-01` 이 제거했고 그 원칙을 여기서도 지킨다.
TRACK_ORDER = (
    ("crypto", "🪙 크립토"),
    ("whale_follow", "🐋 고래 추종"),
    ("stock_us", "📈 주식 US"),
    ("stock_kr", "📉 주식 KR"),
    ("poly", "🎲 폴리마켓"),
)

# 이 미만이면 성적을 단정하지 않는다(C6). `sample_viability.TARGET_SAMPLES` 와 같은 수다.
MIN_SAMPLE = 30

# 텔레그램 한 메시지 한도(C11). 넘치면 **트랙을 빼지 않고 항목을 줄인다.**
MAX_MESSAGE_CHARS = 3800


def _money(value: Any, currency: str) -> str:
    if value is None:
        return "미상"
    digits = 0 if currency == "KRW" else 2
    return f"{float(value):,.{digits}f}"


def _signed(value: Any, digits: int = 2) -> str:
    """부호 있는 금액. `-0` 을 `0` 으로 정규화한다 — `+-0` 이 찍힌 전례가 있다."""
    if value is None:
        return "미상"
    number = 0.0 if float(value) == 0 else float(value)
    body = f"{number:,.{digits}f}"
    return f"+{body}" if number > 0 else body


def _pct(value: Any) -> str:
    if value is None:
        return "미산출"
    number = 0.0 if float(value) == 0 else float(value)
    return f"{'+' if number > 0 else ''}{number:.2f}%"


def capital_lines(track: str, capital: dict[str, Any], *, state: dict[str, Any] | None = None, compact: bool = False) -> list[str]:
    """자본 줄. 실현과 미실현을 **다른 줄**에 둔다(C4).

    ## 자본과 수익률은 같은 기준이다 (WO-FCE-REPORT-DEFECTS-01 7-1)

    `current_capital` = 시작 + **실현**이고 `return_on_capital_pct` = 실현 ÷ 시작이다.
    분자가 같으므로 **자본이 늘면 수익률도 반드시 양수**다. 실측 2026-08-29 에는 아니었다:

        100,000,000 → 100,074,340 KRW (-0.00%)     ← 자본은 NAV, 수익률은 실현

    평가액을 포함한 값(NAV)은 **별도 줄**로 낸다. 같은 줄에 두면 그 줄이 다시 거짓이 된다.
    """
    currency = str(capital.get("currency") or "?")
    start = _money(capital.get("starting_capital"), currency)
    current = _money(capital.get("current_capital"), currency)
    # 현재 자본이 미상이면 통화를 시작값에 붙이고 수익률을 내지 않는다 —
    # `미상 USDC (0.00%)` 는 손익분기처럼 읽힌다.
    if capital.get("current_capital") is None:
        note = capital.get("current_capital_note") or "평가 불가"
        lines = [f"  자본  {start} {currency} → 미상 ({note.split(' —')[0].split(' ·')[0]})"]
    else:
        lines = [f"  자본  {start} → {current} {currency} ({_pct(capital.get('return_on_capital_pct'))}) · 실현 기준"]
    realized = _signed(capital.get("realized_pnl"), 2)
    unrealized = _signed(capital.get("unrealized_pnl"), 2)
    # D4 — 정지 트랙의 미실현은 **닫히지 못한 포지션**이다. 정상 트랙 미실현과 섞지 않는다(7-4).
    halt = " · 정지 중 평가액(청산 안 됨)" if str((state or {}).get("kind") or "") == "halted" and capital.get("unrealized_pnl") else ""
    lines.append(f"  실현  {realized} · 미실현 {unrealized}{halt}")
    nav = capital.get("nav")
    if nav is not None and not compact and capital.get("current_capital") is not None and round(float(nav), 4) != round(float(capital["current_capital"]), 4):
        # 미실현을 포함한 값. **수익률의 분자가 아니다** — 그 사실을 라벨이 말한다.
        lines.append(f"  평가  {_money(nav, currency)} {currency} (미실현 포함 · 수익률 분자 아님)")
    if capital.get("unpriced_positions"):
        lines.append(f"  ⚠️ 평가 불가 포지션 {_money(capital.get('deployed_capital'), currency)} {currency} — NAV 미산출")
    return lines


def activity_line(counts: dict[str, int]) -> str:
    """직전 리포트 이후 건수. 창을 라벨에 박는다 — 창 없는 수는 읽을 수 없다."""
    return f"  오늘  진입 {int(counts.get('entries', 0))} · 청산 {int(counts.get('exits', 0))} · 승 {int(counts.get('wins', 0))}"


def metric_line(metrics: dict[str, Any]) -> str:
    """누적 승률·PF·MDD. **N 을 항상 붙인다**(C6).

    승률과 수익률이 같은 `N` 을 쓴다 — 다른 모집단에서 계산하면 두 수가 서로를 설명하지
    못한다(C3). `_metric_payload` 가 한 번에 내므로 여기서 갈라지지 않는다.
    """
    count = int(metrics.get("trade_count") or 0)
    if count == 0:
        return "  누적  표본 0 — 성적을 내지 않는다"
    win = metrics.get("win_rate_pct")
    profit_factor = metrics.get("profit_factor")
    mdd = metrics.get("mdd_pct")
    parts = [f"N={count}"]
    parts.append(f"승률 {win:.1f}%" if win is not None else "승률 미산출")
    parts.append(f"PF {profit_factor:.2f}" if profit_factor is not None else "PF 미산출")
    if mdd is not None:
        parts.append(f"MDD {mdd:.2f}%")
    tag = f"   [표본 부족 · N<{MIN_SAMPLE}]" if count < MIN_SAMPLE else ""
    return f"  누적  {' · '.join(parts)}{tag}"


def blocked_line(state: dict[str, Any]) -> str | None:
    """막힌 트랙의 상태 줄(C7). 정상처럼 쓰지 않는다."""
    kind = str(state.get("kind") or "")
    detail = str(state.get("detail") or "")
    if kind == "halted":
        return f"  ⛔ 정지 — {detail}"
    if kind == "excluded":
        return f"  ⛔ 검증 대상 제외 — {detail}"
    if kind == "held":
        return f"  ⚠️ 큐 보류 중 — {detail}"
    return None


def track_block(
    track: str,
    label: str,
    *,
    capital: dict[str, Any],
    counts: dict[str, int],
    metrics: dict[str, Any],
    state: dict[str, Any],
    extra: list[str] | None = None,
    compact: bool = False,
) -> list[str]:
    """트랙 한 블록. 막혀 있으면 상태 줄이 성적을 대체한다."""
    lines = [label]
    blocked = blocked_line(state)
    if blocked:
        lines.append(blocked)
    lines.extend(capital_lines(track, capital, state=state, compact=compact))
    if not blocked:
        lines.append(activity_line(counts))
        if not compact:
            lines.append(metric_line(metrics))
    if extra and not compact:
        lines.extend(extra)
    elif blocked and not extra:
        # 막힌 트랙도 표본을 숨기지 않는다 — 0 이면 0 이라고 쓴다(C6).
        lines.append(f"  표본  {int(metrics.get('trade_count') or 0)}건")
    return lines


def action_lines(actions: list[dict[str, Any]]) -> list[str]:
    """조치 필요 블록. **조치가 없으면 통째로 생략한다**(§2-3).

    매일 같은 경고가 붙으면 배경음이 된다. 그래서 조치가 실제로 있을 때만 낸다.
    """
    if not actions:
        return []
    lines = ["", "⚠️ <b>조치 필요</b>"]
    for action in actions:
        lines.append(f"  · {action['title']}")
        if action.get("detail"):
            lines.append(f"    {action['detail']}")
        if action.get("command"):
            lines.append(f"    <code>{action['command']}</code>")
    return lines


def render(report: dict[str, Any], *, max_chars: int = MAX_MESSAGE_CHARS) -> str:
    """메시지 조립. 길이를 넘기면 **항목을 줄이고 트랙은 남긴다**(C11)."""
    for compact in (False, True):
        lines = [
            f"📊 <b>일일 계좌 리포트</b> · {report['as_of_label']}",
            "5트랙 · 실현 합산 없음 · 전부 페이퍼 (실주문 없음)",
            f"직전 리포트 이후: 진입 {report['totals']['entries']}건 · 청산 {report['totals']['exits']}건",
        ]
        for track, label in TRACK_ORDER:
            block = report["tracks"].get(track)
            if block is None:
                continue
            lines.append("")
            lines.extend(
                track_block(
                    track,
                    label,
                    capital=block["capital"],
                    counts=block["counts"],
                    metrics=block["metrics"],
                    state=block["state"],
                    extra=block.get("extra"),
                    compact=compact,
                )
            )
        lines.extend(action_lines(report.get("actions") or []))
        text = "\n".join(lines)
        if len(text) <= max_chars:
            return text
    # 축약해도 넘치면 조치 블록을 뺀다 — 트랙은 절대 빼지 않는다.
    return text[:max_chars]


def window_start(last_sent_at: datetime | None, *, now: datetime, fallback_hours: int = 24) -> datetime:
    """건수 집계 창. 직전 리포트 이후이며 기록이 없으면 24시간이다."""
    if last_sent_at is None:
        return now - timedelta(hours=max(1, int(fallback_hours)))
    return min(last_sent_at, now)


def kst_label(now: datetime) -> str:
    """사용자 시각으로 적는다 — UTC 로 적으면 매일 날짜가 어긋나 보인다."""
    kst = now.astimezone(timezone(timedelta(hours=9)))
    return f"{kst:%Y-%m-%d %H:%M} KST"
