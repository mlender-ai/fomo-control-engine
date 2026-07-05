from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any

from app.notify.bot.callbacks import encode_callback

TELEGRAM_LIMIT = 4096


def split_telegram_text(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > limit:
            if current:
                chunks.append(current.rstrip())
                current = ""
            while len(line) > limit:
                chunks.append(line[:limit])
                line = line[limit:]
        current += line
    if current:
        chunks.append(current.rstrip())
    return chunks


def positions_keyboard(payload: dict[str, Any]) -> list[list[dict[str, str]]]:
    buttons = []
    for item in payload.get("positions", []):
        position = _position(item)
        buttons.append({"text": position.get("symbol", "-"), "callback_data": encode_callback("detail", position.get("symbol", ""))})
    return _rows(buttons, 2)


def detail_keyboard(symbol: str) -> list[list[dict[str, str]]]:
    return [
        [
            {"text": "플랜", "callback_data": encode_callback("plan", symbol)},
            {"text": "인사이트", "callback_data": encode_callback("insight", symbol)},
            {"text": "갱신", "callback_data": encode_callback("refresh", symbol)},
        ],
        [{"text": "◀ 목록", "callback_data": encode_callback("list")}],
    ]


def alert_keyboard(symbol: str) -> list[list[dict[str, str]]]:
    return [[{"text": "상세 보기", "callback_data": encode_callback("detail", symbol)}, {"text": "플랜", "callback_data": encode_callback("plan", symbol)}]]


def format_help() -> str:
    return "\n".join(
        [
            "<b>FOMO Control Engine</b>",
            "읽기 전용 포지션 관제 명령입니다.",
            "",
            "/positions 또는 /p — 전 포지션 요약",
            "/p BASED — 단일 포지션 상세",
            "/plan BASED — 액션 플랜",
            "/insight BASED — 최신 인사이트",
            "/scout — 관심종목 스캔 상위 5",
            "/sim BASED long 10 [0.09] — 진입 시뮬레이션",
            "/review — 최근 복기 3건",
            "/calib — 캘리브레이션 스냅샷",
            "/status — 워커·알림 상태",
            "/mute 2h / /unmute — 알림 일시 무음",
        ]
    )


def format_positions_summary(payload: dict[str, Any]) -> str:
    positions = payload.get("positions", [])
    if not positions:
        return "열린 포지션이 없습니다."
    lines = ["<b>라이브 포지션</b>", f"기준 {_time(payload.get('timestamp'))}", ""]
    for item in positions:
        position = _position(item)
        state = _state(item)
        headline = _headline(item)
        lines.append(
            f"{_severity_emoji(state)} <b>{escape(position.get('symbol', '-'))}</b> "
            f"{_direction(position)} {position.get('leverage', '-')}x · "
            f"PnL {_signed_pct(state.get('pnl_percent'))} · 건강도 {state.get('health_score', '-')}/100"
        )
        lines.append(f"→ {escape(_compact(headline, 90))}")
    return "\n".join(lines)


def format_position_verdict(payload: dict[str, Any]) -> str:
    position = _position(payload)
    state = _state(payload)
    plan = _plan(payload)
    lines = [
        f"{_severity_emoji(state)} <b>{escape(position.get('symbol', '-'))}</b> {_direction(position)} {position.get('leverage', '-')}x · "
        f"{escape(state.get('status_label', '-'))} ({state.get('health_score', '-')}/100)",
        f"PnL {_signed_pct(state.get('pnl_percent'))} ({_pnl_source(state)}) · 기준 {_time(state.get('as_of'))} · {_freshness(payload)}",
        "",
        f"→ {escape(_headline(payload))}",
    ]
    if _liq_missing(payload):
        lines.append("")
        lines.append("⚠ 청산가 미수신 — 수동 확인 필요")
    if plan:
        lines.append("")
        lines.append(_format_plan_block(plan, max_rows=3))
    return "\n".join(lines)


def format_action_plan(payload: dict[str, Any]) -> str:
    position = _position(payload)
    plan = _plan(payload)
    if not plan:
        return f"<b>{escape(position.get('symbol', '-'))} 플랜</b>\n액션 플랜 데이터가 없습니다."
    return f"<b>{escape(position.get('symbol', '-'))} 액션 플랜</b>\n기준 {_time(plan.get('as_of'))}\n\n{_format_plan_block(plan, max_rows=8)}"


def format_insight(payload: dict[str, Any]) -> str:
    position = _position(payload)
    insight = payload.get("latest_insight")
    status = payload.get("insight_status") or {}
    if not insight:
        return f"<b>{escape(position.get('symbol', '-'))} 인사이트</b>\n아직 생성된 인사이트가 없습니다."
    warning = ""
    if status.get("is_stale"):
        warning = f"⚠ 과거 판단 — {escape(status.get('message') or '재생성이 필요합니다.')}\n\n"
    text = insight.get("insight_text") or ""
    return f"<b>{escape(position.get('symbol', '-'))} 인사이트</b>\n{warning}<pre>{escape(_compact(text, 3500))}</pre>"


def format_scout(payload: dict[str, Any]) -> str:
    rows = payload.get("rows", [])
    if not rows:
        return "관심종목 스캔 결과가 없습니다."
    lines = ["<b>스카우트 상위 5</b>"]
    for row in rows[:5]:
        if row.get("error"):
            lines.append(f"• {escape(row.get('symbol', '-'))}: {escape(row['error'])}")
            continue
        distance = row.get("setup_proximity_pct")
        lines.append(
            f"• <b>{escape(row.get('symbol', '-'))}</b> · 근접도 {_nullable_pct(distance)} · "
            f"롱 {row.get('long_score', '-')} / 숏 {row.get('short_score', '-')} · {escape(str(row.get('volume_state', '-')))}"
        )
    return "\n".join(lines)


def format_simulation(result: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"<b>{escape(result.get('symbol', '-'))} 시뮬레이션</b>",
            f"{escape(str(result.get('direction', '-')))} {result.get('leverage', '-')}x · 진입 {_price(result.get('entry_price'))}",
            f"R:R {result.get('rr_ratio') if result.get('rr_ratio') is not None else '-'} · 추정 청산 {_price(result.get('estimated_liquidation'))}",
            f"체크리스트 {result.get('checklist_passed', '-')}/{result.get('checklist_total', '-')}",
            escape(result.get("verdict_line") or ""),
        ]
    )


def format_reviews(trades: list[Any]) -> str:
    if not trades:
        return "최근 종료 트레이드가 없습니다."
    lines = ["<b>최근 복기 3건</b>"]
    for trade in trades[:3]:
        data = _dump(trade)
        lines.append(f"• <b>{escape(data.get('symbol', '-'))}</b> {_direction(data)} · PnL {_signed_pct(data.get('pnl_percent'))} · {escape(_compact(data.get('exit_reason', ''), 80))}")
    return "\n".join(lines)


def format_calibration(payload: dict[str, Any]) -> str:
    totals = payload.get("totals", {})
    invalidation = payload.get("invalidation", {})
    take_profit = payload.get("take_profit", {})
    warning = payload.get("sample_warning", "표본 부족 구간은 결론을 내리지 않습니다.")
    return "\n".join(
        [
            "<b>캘리브레이션</b>",
            f"전체 판단 N={totals.get('total', 0)} · 적중 {totals.get('correct', 0)} · 오판 {totals.get('wrong', 0)}",
            f"무효화 N={invalidation.get('total', 0)} · 적중률 {_nullable_pct(invalidation.get('correct_rate_pct'))}",
            f"익절 N={take_profit.get('total', 0)} · 도달률 {_nullable_pct(take_profit.get('reach_rate_pct'))}",
            f"주의: {escape(warning)}",
        ]
    )


def format_status(payload: dict[str, Any]) -> str:
    jobs = payload.get("jobs", {})
    lines = ["<b>시스템 상태</b>"]
    for name, job in jobs.items():
        lines.append(f"• {escape(name)}: {escape(job.get('status', '-'))} · 성공 {_time(job.get('last_success_at'))} · 실패 {job.get('failures', 0)}")
    notify = payload.get("notifications", {})
    muted_until = notify.get("muted_until")
    lines.append(f"알림: {'무음' if muted_until else '활성'}{f' · {_time(muted_until)}까지' if muted_until else ''}")
    return "\n".join(lines)


def _format_plan_block(plan: dict[str, Any], max_rows: int) -> str:
    rows = ["플랜"]
    invalidation = plan.get("invalidation") or plan.get("engine_invalidation")
    if isinstance(invalidation, dict):
        rows.append(f"├ 무효화 {_price(invalidation.get('price'))} ({_signed_pct(invalidation.get('distance_pct'))}) {escape(invalidation.get('action') or '조건 확인')}")
    targets = [target for target in plan.get("take_profit", []) if isinstance(target, dict)]
    for target in targets[: max(0, max_rows - len(rows))]:
        rows.append(f"├ 익절   {_price(target.get('price'))} ({_signed_pct(target.get('distance_pct'))}) {escape(target.get('action') or '부분 익절 검토')}")
    triggers = [trigger for trigger in plan.get("watch_triggers", []) if isinstance(trigger, dict)]
    for trigger in triggers[: max(0, max_rows - len(rows))]:
        rows.append(f"└ 감시   {escape(trigger.get('condition') or '-')} → {escape(trigger.get('meaning') or '조건 확인')}")
    liquidation = plan.get("liquidation") if isinstance(plan.get("liquidation"), dict) else {}
    if liquidation.get("warning"):
        rows.append(f"⚠ {escape(liquidation['warning'])}")
    return "\n".join(rows)


def _rows(buttons: list[dict[str, str]], size: int) -> list[list[dict[str, str]]]:
    return [buttons[index : index + size] for index in range(0, len(buttons), size)]


def _position(payload: dict[str, Any]) -> dict[str, Any]:
    return _dump(payload.get("position", payload))


def _state(payload: dict[str, Any]) -> dict[str, Any]:
    return _dump(payload.get("state", payload))


def _plan(payload: dict[str, Any]) -> dict[str, Any] | None:
    plan = payload.get("action_plan") or (payload.get("latest_insight") or {}).get("action_plan")
    return plan if isinstance(plan, dict) else None


def _dump(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return {}


def _headline(payload: dict[str, Any]) -> str:
    plan = _plan(payload)
    if plan and plan.get("headline_action"):
        return str(plan["headline_action"])
    return "지금 볼 것: 액션 플랜 근거를 확인하세요."


def _freshness(payload: dict[str, Any]) -> str:
    status = payload.get("insight_status") or {}
    if not status.get("has_insight"):
        return "인사이트 없음"
    if status.get("is_stale"):
        age = status.get("age_minutes")
        return f"과거 판단{f' {age}분 전' if age is not None else ''}"
    return "신선"


def _severity_emoji(state: dict[str, Any]) -> str:
    rank = int(state.get("severity_rank") or 0)
    if rank >= 4:
        return "🔴"
    if rank >= 2:
        return "🟠"
    if rank >= 1:
        return "🟡"
    return "🟢"


def _direction(data: dict[str, Any]) -> str:
    value = data.get("direction")
    if hasattr(value, "value"):
        value = value.value
    return "롱" if value == "long" else "숏" if value == "short" else "-"


def _pnl_source(state: dict[str, Any]) -> str:
    return "거래소" if state.get("pnl_source") == "exchange" else "계산"


def _liq_missing(payload: dict[str, Any]) -> bool:
    position = _position(payload)
    return position.get("status") == "open" and position.get("liquidation_price") is None and float(position.get("leverage") or 0) >= 5


def _signed_pct(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    return f"{number:+.2f}%"


def _nullable_pct(value: Any) -> str:
    if value is None:
        return "표본 부족"
    return _signed_pct(value).replace("+", "")


def _price(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if abs(number) >= 100:
        return f"{number:.2f}"
    if abs(number) >= 1:
        return f"{number:.4f}"
    return f"{number:.6f}"


def _time(value: Any) -> str:
    if not value:
        return "-"
    if isinstance(value, datetime):
        return value.strftime("%H:%M")
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%H:%M")
    except ValueError:
        return escape(str(value))


def _compact(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else f"{text[: max(0, limit - 1)]}…"
