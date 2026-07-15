from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from app.notify.bot.callbacks import encode_callback

TELEGRAM_LIMIT = 4096
DISPLAY_TIMEZONE = ZoneInfo("Asia/Seoul")
ONE_LINER_STANCES = ("상방", "하방", "횡보", "판단불가")
ONE_LINER_MODULES = ("wyckoff", "liquidity", "volume", "harmonic", "levels", "derivatives", "indicators")
ONE_LINER_LABELS = {
    "wyckoff": "와이코프",
    "liquidity": "유동성",
    "volume": "볼륨",
    "harmonic": "하모닉",
    "levels": "레벨",
    "derivatives": "수급",
    "indicators": "지표",
}


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
        buttons.append(
            {
                "text": position.get("symbol", "-"),
                "callback_data": encode_callback("detail", position.get("symbol", "")),
            }
        )
    rows = _rows(buttons, 2)
    if buttons:
        rows.append([{"text": "전체 상세", "callback_data": encode_callback("all_details")}])
    return rows


def main_menu_keyboard() -> list[list[dict[str, str]]]:
    return [
        [
            {"text": "포지션", "callback_data": encode_callback("list")},
            {"text": "스카우트", "callback_data": encode_callback("scout")},
            {"text": "엔진", "callback_data": encode_callback("engine")},
            {"text": "상태", "callback_data": encode_callback("status")},
        ]
    ]


def engine_keyboard() -> list[list[dict[str, str]]]:
    return [
        [
            {"text": "새로고침", "callback_data": encode_callback("engine")},
            {"text": "실계좌 포지션", "callback_data": encode_callback("list")},
            {"text": "시스템 상태", "callback_data": encode_callback("status")},
        ]
    ]


def detail_keyboard(symbol: str) -> list[list[dict[str, str]]]:
    return [
        [
            {"text": "플랜", "callback_data": encode_callback("plan", symbol)},
            {"text": "1줄 판정", "callback_data": encode_callback("one_liners", symbol)},
            {"text": "인사이트", "callback_data": encode_callback("insight", symbol)},
        ],
        [
            {"text": "수급", "callback_data": encode_callback("flow", symbol)},
            {"text": "브리핑", "callback_data": encode_callback("brief", symbol)},
        ],
        [
            {"text": "갱신", "callback_data": encode_callback("refresh", symbol)},
            {"text": "◀ 목록", "callback_data": encode_callback("list")},
        ],
    ]


def insight_keyboard(symbol: str, *, regenerate: bool = False) -> list[list[dict[str, str]]]:
    first_row = [
        {"text": "플랜", "callback_data": encode_callback("plan", symbol)},
        {"text": "갱신", "callback_data": encode_callback("refresh", symbol)},
    ]
    if regenerate:
        first_row.insert(
            0,
            {
                "text": "재생성",
                "callback_data": encode_callback("regen_insight", symbol),
            },
        )
    return [first_row, [{"text": "◀ 목록", "callback_data": encode_callback("list")}]]


def alert_keyboard(symbol: str) -> list[list[dict[str, str]]]:
    return [
        [
            {"text": "상세 보기", "callback_data": encode_callback("detail", symbol)},
            {"text": "플랜", "callback_data": encode_callback("plan", symbol)},
            {"text": "1줄 판정", "callback_data": encode_callback("one_liners", symbol)},
        ]
    ]


def lifecycle_alert_keyboard(rule_id: str, symbol: str) -> list[list[dict[str, str]]]:
    if rule_id == "position_opened":
        return [
            [
                {"text": "플랜", "callback_data": encode_callback("plan", symbol)},
                {"text": "1줄 판정", "callback_data": encode_callback("one_liners", symbol)},
                {"text": "차트", "callback_data": encode_callback("chart", symbol)},
            ]
        ]
    if rule_id == "position_closed":
        return [[{"text": "복기 상세", "callback_data": encode_callback("review")}]]
    if rule_id in {"verdict_changed", "stance_flipped", "evidence_insufficient"}:
        return [
            [
                {"text": "상세", "callback_data": encode_callback("detail", symbol)},
                {"text": "1줄 판정", "callback_data": encode_callback("one_liners", symbol)},
                {"text": "갱신", "callback_data": encode_callback("refresh", symbol)},
            ]
        ]
    if rule_id == "periodic_pulse":
        return [
            [
                {"text": "포지션", "callback_data": encode_callback("list")},
                {"text": "상태", "callback_data": encode_callback("status")},
            ]
        ]
    return alert_keyboard(symbol)


def setup_alert_keyboard(symbol: str, direction: str | None = None) -> list[list[dict[str, str]]]:
    sim_arg = f"{symbol}|{direction or 'long'}"
    return [
        [
            {"text": "상세", "callback_data": encode_callback("scout", symbol)},
            {"text": "시뮬레이션", "callback_data": encode_callback("sim", sim_arg)},
            {"text": "브리핑", "callback_data": encode_callback("brief", symbol)},
        ]
    ]


def scout_tracking_keyboard(symbol: str) -> list[list[dict[str, str]]]:
    return [
        [
            {"text": "갱신", "callback_data": encode_callback("scout", symbol)},
            {"text": "브리핑", "callback_data": encode_callback("brief", symbol)},
            {"text": "추적 중지", "callback_data": encode_callback("unscout", symbol)},
        ],
        [{"text": "◀ 스카우트", "callback_data": encode_callback("scout")}],
    ]


def format_help() -> str:
    return "\n".join(
        [
            "<b>FOMO Control Engine</b>",
            "읽기 전용 포지션 관제 명령입니다.",
            "",
            "/positions, /position 또는 /p — 전 포지션 요약",
            "/positions_full 또는 /pf — 보유 포지션별 상세 관제",
            "/position BASED 또는 /p BASED — 단일 포지션 상세",
            "/plan BASED — 액션 플랜",
            "/insight BASED — 최신 인사이트",
            "/flow BASED — 펀딩·OI·롱숏비",
            "/brief BASED — 애널리스트 브리핑",
            "/scout — 티커 입력 안내와 현재 스카우트 추적 목록",
            "/scout SOL — SOLUSDT 스카우트 지속 추적 시작",
            "SOL 또는 SOLUSDT — 티커만 보내도 스카우트 추적 시작",
            "/unscout SOL — 스카우트 추적 중지",
            "/q SOL — 심볼 즉답 판정",
            "/intents — 등록한 진입 의도",
            "/intent TSLA long 240-250 — 진입 의도 등록",
            "/sim BASED long 10 [0.09] — 진입 시뮬레이션",
            "/review — 최근 복기 3건",
            "/calib — 캘리브레이션 스냅샷",
            "/perf — 계좌 성과 요약",
            "/engine — 엔진 페이퍼 대결 요약 · /paper — 성과·포지션·진입 근거",
            "/whales · /whale add 0x주소 [별칭] — Hyperliquid 고래 관측",
            "/experiments — 파라미터 자율 예정·섀도 실험",
            "/veto ID — 파라미터 자율 채택 거부",
            "/status — 워커·알림 상태",
            "/mute 2h / /unmute — 알림 일시 무음",
        ]
    )


def format_entry_intents(payload: dict[str, Any]) -> str:
    intents = payload.get("intents") if isinstance(payload.get("intents"), list) else []
    if not intents:
        return "등록된 진입 의도가 없습니다."
    lines = ["<b>진입 의도</b>", "사용자 등록 존 기준 · 알림은 조건 충족 통보입니다.", ""]
    for intent in intents[:12]:
        if not isinstance(intent, dict):
            continue
        direction = "롱" if intent.get("direction") == "long" else "숏"
        zone = f"{_price(intent.get('zone_lower'))}–{_price(intent.get('zone_upper'))}"
        conditions = intent.get("conditions") if isinstance(intent.get("conditions"), list) else []
        expires = _time(intent.get("expires_at"))
        lines.append(f"📍 <b>{escape(str(intent.get('symbol') or '-'))}</b> {direction} · {zone} · {escape(str(intent.get('status') or '-'))}")
        lines.append(f"조건 {escape(', '.join(map(str, conditions)) or 'price_in_zone')} · 만료 {expires}")
        note = str(intent.get("note") or "").strip()
        if note:
            lines.append(f"메모 {escape(note)}")
        lines.append("")
    return "\n".join(lines).strip()


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
    one_liners = format_one_liner_strip(payload)
    if one_liners:
        lines.extend(["", one_liners])
    flow_line = _flow_line_from_position_payload(payload)
    if flow_line:
        lines.append(flow_line)
    money_flow_block = _money_flow_block_from_position_payload(payload)
    if money_flow_block:
        lines.extend(["", money_flow_block])
    liquidity_line = _liquidity_line_from_payload(payload)
    if liquidity_line:
        lines.append(liquidity_line)
    briefing_line = _briefing_line_from_payload(payload)
    if briefing_line:
        lines.append(briefing_line)
    if _liq_missing(payload):
        lines.append("")
        lines.append("⚠ 청산가 미수신 — 수동 확인 필요")
    if plan:
        lines.append("")
        lines.append(_format_plan_block(plan, max_rows=3))
    return "\n".join(lines)


def format_one_liner_strip(payload_or_one_liners: dict[str, Any]) -> str:
    one_liners = _one_liners_from_payload(payload_or_one_liners)
    if not one_liners:
        return ""
    raw_lines = one_liners.get("lines")
    if not isinstance(raw_lines, list):
        return ""
    by_module = {str(line.get("module")): line for line in raw_lines if isinstance(line, dict) and line.get("module")}
    lines = ["<b>TA 1줄</b>"]
    counts = {stance: 0 for stance in ONE_LINER_STANCES}
    for module in ONE_LINER_MODULES:
        line = by_module.get(module) or {}
        label = str(line.get("module_label") or ONE_LINER_LABELS[module])
        stance = str(line.get("stance") or "판단불가")
        phrase = str(line.get("phrase") or "데이터 부족")
        if stance not in counts:
            stance = "판단불가"
        counts[stance] += 1
        lines.append(f"{escape(label)} {_stance_dot(stance)} {escape(_compact(phrase, 32))}")
    raw_counts = one_liners.get("counts")
    if isinstance(raw_counts, dict):
        for stance in ONE_LINER_STANCES:
            value = raw_counts.get(stance)
            if isinstance(value, int):
                counts[stance] = value
    conflict = counts["상방"] > 0 and counts["하방"] > 0
    summary = f"종합: 상방 {counts['상방']} · 하방 {counts['하방']} · 중립 {counts['횡보']} · 판단불가 {counts['판단불가']}"
    if conflict:
        summary += " · 충돌"
    lines.append(summary)
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
    return f"<b>{escape(position.get('symbol', '-'))} 인사이트</b>\n{warning}{escape(_compact(text, 3800))}"


def format_flow(payload: dict[str, Any]) -> str:
    symbol = str(payload.get("symbol") or "-").upper()
    latest = _dump(payload.get("latest"))
    summary = _dump(payload.get("summary"))
    coinglass = _dump(payload.get("coinglass"))
    signals = _dump(payload.get("signals"))
    if not latest:
        return f"<b>{escape(symbol)} 수급</b>\n아직 파생 데이터가 없습니다. 워커의 collect_derivatives 실행 후 다시 확인하세요."
    funding = _dump(signals.get("funding_state"))
    divergence = _dump(signals.get("oi_price_divergence"))
    crowding = _dump(signals.get("crowding_score"))

    lines = [
        f"<b>{escape(symbol)} 수급</b>",
        f"기준 {_time(latest.get('as_of'))} · 출처 {escape(str(latest.get('provider') or '-'))} · 상태 {escape(str(latest.get('source_status') or '-'))}",
        "",
        f"펀딩 {_funding(latest.get('funding_rate'))} · 다음 정산 {_time(latest.get('next_funding_time'))}",
        f"OI {_number(latest.get('open_interest'))} · 변화 {_nullable_pct(latest.get('open_interest_change_pct'))}",
        f"롱/숏 {_ratio_to_pct(latest.get('long_account_ratio'))} / {_ratio_to_pct(latest.get('short_account_ratio'))} · 비율 {_number(latest.get('long_short_ratio'))}",
        f"쏠림 점수 {_number(crowding.get('score'))} · 펀딩 상태 {escape(str(funding.get('label') or '표본 부족'))}",
        f"OI/가격 {escape(str(divergence.get('label') or '표본 부족'))}",
        "",
        f"→ {escape(summary.get('headline') or '수급 상태를 판단할 데이터가 부족합니다.')}",
    ]
    notes = [str(note) for note in latest.get("notes", []) if note]
    if notes:
        lines.append("")
        lines.append("주의: " + escape(_compact(" · ".join(notes), 220)))
    if coinglass:
        status = coinglass.get("source_status") or "locked"
        lines.append("")
        lines.append(f"Coinglass: {escape(str(status))} · {escape(_compact(' · '.join(str(note) for note in coinglass.get('notes', []) if note), 180))}")
    return "\n".join(lines)


def format_briefing(payload: dict[str, Any]) -> str:
    symbol = str(payload.get("symbol") or (payload.get("position") or {}).get("symbol") or "-").upper()
    briefing = payload.get("analyst_briefing") if isinstance(payload.get("analyst_briefing"), dict) else payload
    if not isinstance(briefing, dict) or not briefing:
        return f"<b>{escape(symbol)} 브리핑</b>\n브리핑 데이터가 아직 없습니다."
    text = briefing.get("text")
    if not text:
        confluence = _dump(briefing.get("confluence"))
        stance = confluence.get("stance_label") or confluence.get("stance") or "판정 보류"
        text = f"{symbol} 브리핑\n스탠스: {stance}\n근거 데이터가 부족합니다."
    return escape(_compact(str(text), 3900))


def format_scout(payload: dict[str, Any]) -> str:
    rows = payload.get("rows", [])
    if not rows:
        return "관심종목 스캔 결과가 없습니다."
    armed = payload.get("armed_setups") if isinstance(payload.get("armed_setups"), list) else []
    armed_by_symbol = {}
    for setup in armed:
        if isinstance(setup, dict) and setup.get("status") == "armed":
            armed_by_symbol.setdefault(str(setup.get("symbol") or "").upper(), setup)
    lines = ["<b>스카우트 상위 5</b>"]
    for row in rows[:5]:
        if row.get("error"):
            lines.append(f"• {escape(row.get('symbol', '-'))}: {escape(row['error'])}")
            continue
        distance = row.get("setup_proximity_pct")
        setup = armed_by_symbol.get(str(row.get("symbol") or "").upper())
        armed_label = f" · 🎯 {escape(str(setup.get('trigger_label')))}" if setup else ""
        lines.append(
            f"• <b>{escape(row.get('symbol', '-'))}</b> · 근접도 {_nullable_pct(distance)} · "
            f"롱 {row.get('long_score', '-')} / 숏 {row.get('short_score', '-')} · "
            f"쏠림 {_number(row.get('crowding_score'))} · {escape(str(row.get('funding_state') or row.get('volume_state', '-')))}{armed_label}"
        )
    return "\n".join(lines)


def format_scout_prompt(payload: dict[str, Any] | None = None) -> str:
    payload = payload or {}
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    lines = [
        "<b>스카우트 추적</b>",
        "보고 싶은 티커를 보내세요.",
        "예: <code>BTC</code>, <code>ETHUSDT</code>, <code>TSLA</code>",
        "",
        "/scout BTC 로도 시작할 수 있습니다.",
        "포지션 진입이 감지되면 스카우트는 자동 종료되고 포지션 관제로 전환됩니다.",
        "",
    ]
    if not items:
        lines.append("현재 추적 중인 스카우트가 없습니다.")
        return "\n".join(lines)
    lines.append("<b>현재 추적</b>")
    for item in items[:12]:
        if not isinstance(item, dict):
            continue
        symbol = escape(str(item.get("symbol") or "-").upper())
        timeframe = escape(str(item.get("default_timeframe") or "4h"))
        note = str(item.get("note") or "").strip()
        suffix = f" · {escape(note)}" if note and note != "telegram scout tracking" else ""
        lines.append(f"• <b>{symbol}</b> · {timeframe}{suffix}")
    return "\n".join(lines)


def format_scout_quick_answer(payload: dict[str, Any]) -> str:
    symbol = str(payload.get("symbol") or "-").upper()
    timeframe = str(payload.get("timeframe") or "4h")
    one_liners = _one_liners_from_payload(payload)
    strip = format_one_liner_strip(one_liners) if one_liners else ""
    summary = _dump(payload.get("summary"))
    confluence = _scout_confluence(payload)
    tilt = _scout_tilt_label(summary, one_liners, confluence)
    as_of = _time(payload.get("as_of"))
    lines = [
        f"<b>{escape(symbol)}</b> · 현재 종합 판정 · 기준 {as_of} KST · {escape(timeframe)}",
        tilt,
    ]
    lines.extend(_scout_decision_context(confluence))
    if strip:
        lines.extend(["", strip])
    else:
        reasons = _scout_reason_line(summary)
        lines.append(f"근거: {escape(reasons or '모듈별 판정 데이터가 아직 없습니다.')}")
    options = _dump(_dump(payload.get("analysis")).get("options"))
    if options.get("available") is True:
        volume_date = str(options.get("volume_date") or "최근 완료일")
        lines.extend(
            [
                "",
                f"<b>옵션 계약 · {escape(str(options.get('underlying') or symbol))} · OCC</b>",
                f"OI(전일 결제) · 콜 {_number(options.get('call_open_interest'))} · 풋 {_number(options.get('put_open_interest'))} · P/C {_ratio(options.get('put_call_oi_ratio'))}",
                f"계약량({escape(volume_date)}) · 콜 {_number(options.get('call_volume'))} · 풋 {_number(options.get('put_volume'))} · P/C {_ratio(options.get('put_call_volume_ratio'))}",
                "관측 전용 · 종합 방향 판정에는 미반영",
            ]
        )
    trigger = _scout_trigger(summary)
    if trigger:
        lines.extend(["", f"트리거까지: {escape(trigger)}"])
    lines.append("셋업 트리거는 개별 조건 알림입니다. 종합과 충돌하면 양측 근거와 상위 추세를 함께 확인하세요.")
    lines.append("과거 통계와 현재 판정은 판단 보조입니다.")
    return "\n".join(lines)


def format_scout_tracking(payload: dict[str, Any]) -> str:
    tracking = _dump(payload.get("tracking"))
    mode = str(tracking.get("mode") or "scout")
    symbol = str(payload.get("symbol") or "-").upper()
    if mode == "position":
        return "\n".join(
            [
                f"<b>{escape(symbol)}</b>",
                "이미 열린 포지션입니다.",
                escape(str(tracking.get("message") or "스카우트 추적은 포지션 관제로 전환됩니다.")),
            ]
        )
    quick = format_scout_quick_answer(payload)
    return "\n".join(
        [
            quick,
            "",
            f"📡 <b>스카우트 추적 시작</b> · {escape(symbol)}",
            escape(str(tracking.get("message") or "포지션 진입 전까지 워커가 계속 관제합니다.")),
            f"중지: /unscout {escape(symbol.replace('USDT', ''))}",
        ]
    )


def format_scout_stopped(payload: dict[str, Any]) -> str:
    symbol = str(payload.get("symbol") or "-").upper()
    tracking = _dump(payload.get("tracking"))
    return "\n".join(
        [
            f"<b>{escape(symbol)}</b>",
            escape(str(tracking.get("message") or "스카우트 추적을 중지했습니다.")),
        ]
    )


def format_improvement_digest(payload: dict[str, Any]) -> str:
    totals = _dump(payload.get("totals"))
    highlights = [str(item) for item in payload.get("highlights", []) if item]
    tested = int(totals.get("tested") or 0)
    accuracy = _nullable_pct(totals.get("accuracy_pct"))
    if tested < 10:
        headline = "이번 주 유의미한 개선 없음"
        reason = f"검증 표본 N={tested} · 결론 유보"
    elif highlights:
        headline = _compact(highlights[0], 92)
        reason = f"검증 N={tested} · 적중률 {accuracy}"
    else:
        headline = "이번 주 유의미한 개선 없음"
        reason = f"검증 N={tested} · 새 과신/개선 구간 없음"
    scheduled = payload.get("scheduled_suggestions_count")
    experiments = payload.get("experiment_suggestions_count")
    if scheduled is None:
        scheduled = len(payload.get("scheduled_suggestions", []) or [])
    if experiments is None:
        experiments = len(payload.get("experiment_suggestions", []) or [])
    return "\n".join(
        [
            "<b>개선 카드</b>",
            f"• {escape(headline)}",
            f"• {escape(reason)}",
            f"• 자율 예정 {scheduled}건 · 섀도 실험 {experiments}건",
            "• 표본 부족 구간은 결론을 보류합니다.",
        ]
    )


def format_simulation(result: dict[str, Any]) -> str:
    rr_display = result.get("rr_ratio_display")
    if result.get("invalidation_too_close"):
        rr_display = "산출 불가 · 무효화 너무 가까움"
    elif rr_display is None:
        rr_display = result.get("rr_ratio") if result.get("rr_ratio") is not None else "-"
    lines = [
        f"<b>{escape(result.get('symbol', '-'))} 시뮬레이션</b>",
        f"{escape(str(result.get('direction', '-')))} {result.get('leverage', '-')}x · 진입 {_price(result.get('entry_price'))}",
        f"R:R {rr_display} · 추정 청산 {_price(result.get('estimated_liquidation'))}",
        f"체크리스트 {result.get('checklist_passed', '-')}/{result.get('checklist_total', '-')}",
        escape(result.get("verdict_line") or ""),
    ]
    if result.get("briefing_direction_conflict"):
        lines.append("⚠ 브리핑 스탠스와 반대 방향 시뮬레이션입니다.")
    briefing_line = _briefing_line_from_payload(result)
    if briefing_line:
        lines.append(briefing_line)
    return "\n".join(lines)


def format_reviews(trades: list[Any]) -> str:
    if not trades:
        return "최근 종료 트레이드가 없습니다."
    lines = ["<b>최근 복기 3건</b>"]
    for trade in trades[:3]:
        data = _dump(trade)
        lines.append(
            f"• <b>{escape(data.get('symbol', '-'))}</b> {_direction(data)} · PnL {_signed_pct(data.get('pnl_percent'))} · {escape(_compact(data.get('exit_reason', ''), 80))}"
        )
    return "\n".join(lines)


def format_calibration(payload: dict[str, Any]) -> str:
    totals = payload.get("totals", {})
    invalidation = payload.get("invalidation", {})
    take_profit = payload.get("take_profit", {})
    confidence_curve = payload.get("confidence_curve", [])
    status_counts = payload.get("suggestion_status_counts", {})
    autonomy = payload.get("autonomy", {})
    warning = payload.get("sample_warning", "표본 부족 구간은 결론을 내리지 않습니다.")
    lines = [
        "<b>캘리브레이션</b>",
        f"전체 판단 N={totals.get('total', 0)} · 검증 {totals.get('tested', 0)} · 적중률 {_nullable_pct(totals.get('accuracy_pct'))}",
        f"무효화 N={invalidation.get('total', 0)} · 적중률 {_nullable_pct(invalidation.get('accuracy_pct'))}",
        f"익절 N={take_profit.get('total', 0)} · 도달률 {_nullable_pct(take_profit.get('reach_rate_pct'))}",
        f"예정 {status_counts.get('scheduled', 0)}건 · 실험 {status_counts.get('experiment', 0)}건 · 자율 적용 {status_counts.get('adopted', 0)}건",
        f"거부권 창 {autonomy.get('veto_window_hours', 48)}h · /veto <id> 로 차단",
    ]
    if confidence_curve:
        lines.append("신뢰도 곡선")
        for bucket in confidence_curve[:4]:
            lines.append(
                f"• {escape(str(bucket.get('bucket', '-')))}: N={bucket.get('tested', 0)} · "
                f"적중 {_nullable_pct(bucket.get('accuracy_pct'))} · {escape(str(bucket.get('conclusion', '-')))}"
            )
    lines.append(f"주의: {escape(warning)}")
    return "\n".join(lines)


def format_weekly_calibration(payload: dict[str, Any]) -> str:
    totals = payload.get("totals", {})
    period = payload.get("period", {})
    highlights = [str(item) for item in payload.get("highlights", []) if item]
    best = payload.get("best_judgment") if isinstance(payload.get("best_judgment"), dict) else {}
    worst = payload.get("worst_judgment") if isinstance(payload.get("worst_judgment"), dict) else {}
    curve = payload.get("confidence_curve", [])
    overconfident = [
        item for item in curve if isinstance(item, dict) and item.get("calibration_state") == "overconfident" and int(item.get("tested") or 0) >= 10
    ]
    totals_ci = totals.get("accuracy_ci")
    ci_text = f" (CI {totals_ci[0]}~{totals_ci[1]}%)" if isinstance(totals_ci, list) and len(totals_ci) == 2 else ""
    lines = [
        format_improvement_digest(payload),
        "",
        "<b>주간 판단 성적표</b>",
        f"{escape(str(period.get('label', '최근 7일')))} · 검증 N={totals.get('tested', 0)} · 적중률 {_nullable_pct(totals.get('accuracy_pct'))}{ci_text}",
    ]
    if highlights:
        lines.extend(f"• {escape(item)}" for item in highlights[:3])
    else:
        lines.append("• 주간 표본이 아직 충분하지 않습니다.")
    if best:
        lines.append(f"최고 판단: {escape(str(best.get('judgment_type', '-')))} · {escape(str(best.get('detail', '-')))}")
    if worst:
        lines.append(f"최악 판단: {escape(str(worst.get('judgment_type', '-')))} · {escape(str(worst.get('detail', '-')))}")
    if overconfident:
        first = overconfident[0]
        lines.append(f"과신 구간: {escape(str(first.get('bucket', '-')))} · N={first.get('tested', 0)} · 실제 {_nullable_pct(first.get('accuracy_pct'))}")
    response_summary = payload.get("alert_response_summary") if isinstance(payload.get("alert_response_summary"), dict) else {}
    response_total = response_summary.get("total") if isinstance(response_summary.get("total"), dict) else {}
    if response_total:
        lines.append(
            f"알림 대응: N={response_total.get('total', 0)} · good {response_total.get('response_good', 0)} · costly {response_total.get('response_costly', 0)}"
        )
        behavior = response_summary.get("behavior_summary")
        if behavior:
            lines.append(f"• {escape(str(behavior))}")
    scout_summary = payload.get("scout_setup_summary") if isinstance(payload.get("scout_setup_summary"), dict) else {}
    if scout_summary:
        lines.append(
            "진입 전 셋업: "
            f"N={scout_summary.get('total', 0)} · 검증 {scout_summary.get('tested', 0)} · 적중률 {_nullable_pct(scout_summary.get('accuracy_pct'))}"
        )
    briefing = payload.get("briefing_performance") if isinstance(payload.get("briefing_performance"), dict) else {}
    if briefing:
        summary = briefing.get("summary") if isinstance(briefing.get("summary"), dict) else {}
        lines.append(f"브리핑 성적: N={briefing.get('total', 0)} · 검증 {summary.get('tested', 0)} · 적중률 {_nullable_pct(summary.get('accuracy_pct'))}")
    performance = payload.get("performance") if isinstance(payload.get("performance"), dict) else {}
    overall = performance.get("overall") if isinstance(performance.get("overall"), dict) else {}
    if overall:
        lines.append(
            "계좌 성과: "
            f"N={overall.get('sample_size', 0)} · 순손익 {_number(overall.get('net_profit_usdt'))} USDT · "
            f"MDD {_signed_pct(overall.get('max_drawdown_pct'))} · PF {escape(str(overall.get('profit_factor') if overall.get('profit_factor') is not None else '유보'))}"
        )
    scheduled = payload.get("scheduled_suggestions", [])
    experiments = payload.get("experiment_suggestions", [])
    if scheduled or experiments:
        lines.append("")
        if scheduled:
            lines.append(f"거부권 대기 {payload.get('scheduled_suggestions_count', len(scheduled))}건")
            for suggestion in scheduled[:3]:
                deadline = suggestion.get("autonomy", {}).get("veto_deadline_at") if isinstance(suggestion.get("autonomy"), dict) else None
                lines.append(
                    f"• {escape(suggestion.get('title', '-'))} · N={suggestion.get('sample_size', 0)} · /veto {escape(str(suggestion.get('id', '')))} · 기한 {escape(str(deadline or '-'))}"
                )
        if experiments:
            lines.append(f"섀도 실험 {payload.get('experiment_suggestions_count', len(experiments))}건")
            for suggestion in experiments[:3]:
                lines.append(f"• {escape(suggestion.get('title', '-'))} · N={suggestion.get('sample_size', 0)}")
    audit = payload.get("self_audit") if isinstance(payload.get("self_audit"), dict) else {}
    if audit:
        lines.extend(_format_self_audit(audit))
    candidate_review = payload.get("candidate_review") if isinstance(payload.get("candidate_review"), dict) else {}
    candidate_items = [item for item in candidate_review.get("items", []) if isinstance(item, dict)]
    if candidate_items:
        lines.extend(["", "<b>Candidate 심사 현황</b>"])
        for item in candidate_items:
            ci = item.get("win_1r_ci")
            ci_text = f" · CI {ci[0]}~{ci[1]}%" if isinstance(ci, list) and len(ci) == 2 else ""
            lines.append(
                f"• {escape(str(item.get('label', item.get('engine', '-'))))}: "
                f"N={item.get('sample_size', 0)} · 1R {_nullable_pct(item.get('win_1r_pct'))}{ci_text} · "
                f"승격까지 {item.get('remaining_samples', 0)}표본 · {escape(str(item.get('status', 'candidate')))}"
            )
    lines.append("")
    lines.append(escape(payload.get("sample_warning") or "표본 N < 10 구간은 결론을 보류합니다."))
    return "\n".join(lines)


def _format_self_audit(audit: dict[str, Any]) -> list[str]:
    """WO-37 셀프 오딧 — 자율/승인 2단 가시화."""
    lines = ["", "<b>자율 검증 리포트</b>"]
    if audit.get("critical"):
        lines.append("🚨 전 시그니처 격리 — 엔진 전면 불신 상태 · 신규 자율 강등 동결")
    did = audit.get("engine_did_autonomously") if isinstance(audit.get("engine_did_autonomously"), dict) else {}
    waiting = audit.get("awaiting_approval") if isinstance(audit.get("awaiting_approval"), dict) else {}
    lines.append(f"■ {escape(str(did.get('label', '엔진이 스스로 한 일')))} ({did.get('count', 0)}건)")
    for row in (did.get("transitions") or [])[:5]:
        regime = f" · {escape(str(row.get('regime')))} 한정" if row.get("regime") else ""
        lines.append(
            f"• {escape(str(row.get('signature_key', '-')))}: {escape(str(row.get('from')))}→{escape(str(row.get('to')))}{regime} ({escape(str(row.get('reason', '-')))})"
        )
    if not (did.get("transitions") or []):
        lines.append("• 이번 주 자율 전이 없음")
    lines.append(f"■ {escape(str(waiting.get('label', '승인 대기 중인 일')))} ({waiting.get('count', 0)}건)")
    for row in (waiting.get("transitions") or [])[:5]:
        lines.append(f"• {escape(str(row.get('signature_key', '-')))}: {escape(str(row.get('transition', '-')))} ({escape(str(row.get('reason', '-')))})")
    pending = waiting.get("recovery_pending") or []
    if pending:
        lines.append(f"• 복귀 제안 대기 {len(pending)}건: {escape(', '.join(str(k) for k in pending[:3]))}")
    if not (waiting.get("transitions") or []) and not pending:
        lines.append("• 승인 대기 항목 없음")
    meta = audit.get("meta_integrity") if isinstance(audit.get("meta_integrity"), dict) else {}
    if meta.get("misjudgment_rate_pct") is not None:
        lines.append(f"자율 강등 오판율: {_nullable_pct(meta.get('misjudgment_rate_pct'))} (N={meta.get('autonomous_downgrades', 0)})")
    return lines


def format_performance(payload: dict[str, Any]) -> str:
    overall = payload.get("overall") if isinstance(payload.get("overall"), dict) else {}
    guard = payload.get("mdd_guard") if isinstance(payload.get("mdd_guard"), dict) else {}
    cross = payload.get("scoreboard_cross_view") if isinstance(payload.get("scoreboard_cross_view"), dict) else {}
    ruin = overall.get("risk_of_ruin") if isinstance(overall.get("risk_of_ruin"), dict) else {}
    warnings = [str(item) for item in overall.get("warnings", []) if item]
    lines = [
        "<b>계좌 성과</b>",
        f"기준 {_time(payload.get('as_of'))} · 종료 거래 N={overall.get('sample_size', 0)}",
        f"순손익 {_number(overall.get('net_profit_usdt'))} USDT · 승률 {_nullable_pct(overall.get('win_rate_pct'))} · 평균 R {escape(str(overall.get('avg_r') if overall.get('avg_r') is not None else '-'))}",
        f"PF {escape(str(overall.get('profit_factor') if overall.get('profit_factor') is not None else '유보'))} · MDD {_signed_pct(overall.get('max_drawdown_pct'))} · Sortino {escape(str(overall.get('sortino') if overall.get('sortino') is not None else '유보'))}",
        f"리커버리 {escape(str(overall.get('recovery_factor') if overall.get('recovery_factor') is not None else '유보'))} · 파산확률 {_nullable_pct(ruin.get('probability_pct') if ruin.get('published') else None)}",
    ]
    if guard.get("configured"):
        lines.append(f"월 MDD 한도 {guard.get('limit_pct')}% · 사용률 {guard.get('usage_pct')}% · {escape(str(guard.get('status', '-')))}")
    else:
        lines.append("월 MDD 한도: 미설정")
    lines.append(
        f"3성적표: 엔진 적중·계좌 손실 {cross.get('engine_right_but_account_lost', 0)}건 · "
        f"엔진 오판 우세 {cross.get('engine_wrong_dominant', 0)}건 · 셋업 경유 {cross.get('setup_linked_trades', 0)}건"
    )
    if warnings:
        lines.append("")
        lines.extend(f"주의: {escape(item)}" for item in warnings[:3])
    lines.append("")
    lines.append(escape(str(payload.get("disclaimer") or "성과 지표는 표본과 기준 자본을 함께 봐야 합니다.")))
    return "\n".join(lines)


def format_engine_scoreboard(payload: dict[str, Any]) -> str:
    scoreboard = payload.get("scoreboard") if isinstance(payload.get("scoreboard"), dict) else payload
    engine = _dump(scoreboard.get("engine"))
    user = _dump(scoreboard.get("user"))
    rolling = _dump(scoreboard.get("rolling_4w"))
    recent = _dump(_dump(scoreboard.get("recent_28d")).get("engine"))
    open_trades = payload.get("open_trades") if isinstance(payload.get("open_trades"), list) else []
    verdict = "엔진 우세" if rolling.get("engine_leading") else "우세 미확정"
    scored = int(engine.get("scored_trade_count") if engine.get("scored_trade_count") is not None else engine.get("trade_count") or 0)
    total = int(engine.get("trade_count") or 0)
    neutral = int(engine.get("neutral_count") or 0)
    lines = [
        "<b>🤖 엔진 트레이딩 대결 · 페이퍼 현황</b>",
        f"4주 판정: <b>{verdict}</b> · 열린 포지션 {len(open_trades)}개",
        f"엔진 수익 {_signed_pct(engine.get('net_return_pct'))} · 승률 {_nullable_pct(engine.get('win_rate_pct'))} · PF {_ratio(engine.get('profit_factor'))} · MDD {_nullable_pct(engine.get('mdd_pct'))}",
        f"표본 판정 N={scored} / 전체 {total}{f' · 시간종료 중립 {neutral}' if neutral else ''}",
    ]
    if recent:
        recent_scored = int(recent.get("scored_trade_count") if recent.get("scored_trade_count") is not None else recent.get("trade_count") or 0)
        lines.append(f"최근 28일: {_signed_pct(recent.get('net_return_pct'))} · 승률 {_nullable_pct(recent.get('win_rate_pct'))} · 판정 N={recent_scored}")
    if scored < 10:
        lines.append("⚠️ 종료 표본 10건 미만 · 승률 판정 유보")
    lines.extend(["", "<b>현재 포지션</b>"])
    if not open_trades:
        lines.append("열린 포지션 없음 · 확정 캔들 진입 게이트를 감시 중입니다.")
    for trade in open_trades[:8]:
        lines.extend(_paper_position_lines(_dump(trade)))
    lines.extend(
        [
            "",
            f"대결: 엔진 {_signed_pct(engine.get('net_return_pct'))} (N={total}) | 나 {_signed_pct(user.get('net_return_pct'))} (N={int(user.get('trade_count') or 0)})",
            f"거래수: 엔진 N={total} | 나 N={int(user.get('trade_count') or 0)}",
            "실주문이 아닌 엔진 가상 거래 · 현재 평가는 기발생 비용 반영",
        ]
    )
    return "\n".join(lines)


def _paper_position_lines(trade: dict[str, Any]) -> list[str]:
    monitor = _dump(trade.get("exit_monitor"))
    direction = "롱" if trade.get("direction") == "long" else "숏"
    pnl_pct = monitor.get("mark_net_return_pct")
    pnl_usdt = monitor.get("mark_net_pnl_usdt")
    pnl_value = _float_or_none(pnl_pct)
    pnl_icon = "⚪️" if pnl_value is None else "🟢" if pnl_value >= 0 else "🔴"
    stage = "TP1 완료 · 잔여 50% · 본전 스탑" if trade.get("partial_exit_at") else "TP1 대기 · 전량 보유"
    leverage = _number(trade.get("leverage"))
    lines = [
        "",
        f"{pnl_icon} <b>{escape(str(trade.get('symbol') or '-'))} · {direction} {leverage}x</b> · {_signed_pct(pnl_pct)} ({_signed_number(pnl_usdt)} USDT)",
        f"진입 {_price(trade.get('entry_price'))} → 현재 {_price(monitor.get('mark_price'))} · {int(trade.get('holding_bars') or 0)}캔들",
        f"스탑 {_price(trade.get('stop_price') or trade.get('invalidation_price'))} ({_signed_pct(monitor.get('invalidation_distance_pct'))}) · TP1 {_price(trade.get('take_profit_price'))} ({_positive_distance(monitor.get('take_profit_distance_pct'))})",
    ]
    if trade.get("take_profit_2_price") is not None:
        lines[-1] += f" · TP2 {_price(trade.get('take_profit_2_price'))} ({_positive_distance(monitor.get('take_profit_2_distance_pct'))})"
    current_stance = _paper_current_stance(trade)
    if current_stance:
        lines.append(f"현재 판정: {current_stance}")
    lines.append(f"진행: {stage}")
    lines.append(f"진입 근거: {escape(_paper_entry_reason(trade))}")
    return lines


def _paper_entry_reason(trade: dict[str, Any]) -> str:
    evidence = _dump(trade.get("entry_evidence"))
    items = evidence.get("items") if isinstance(evidence.get("items"), list) else []
    reasons: list[str] = []
    for item in items:
        row = _dump(item)
        claim = _compact(str(row.get("claim") or row.get("label") or "").strip(), 34)
        if claim and claim not in reasons:
            reasons.append(claim)
    if reasons:
        return " + ".join(reasons[:3])
    mode = str(evidence.get("entry_mode") or "")
    if mode == "validation_bootstrap":
        return "4주 검증 시작 시드 · 당시 종합 스탠스와 구조 근거"
    stance = str(_dump(trade.get("stance_snapshot")).get("stance") or "")
    return f"확정 스탠스 {stance} · 진입 게이트 통과" if stance else "검증 게이트 통과"


def _paper_current_stance(trade: dict[str, Any]) -> str:
    state = _dump(trade.get("current_stance"))
    stance = str(state.get("stance") or "")
    if not stance:
        return ""
    label = {
        "long": "상방 우세",
        "long_leaning": "상방 우세",
        "short": "하방 우세",
        "short_leaning": "하방 우세",
        "conflicted": "혼조",
        "insufficient": "근거 부족",
    }.get(stance, stance)
    if state.get("transitioning") is True:
        return f"{label} · 전환 관찰 중"
    aligned = (trade.get("direction") == "long" and stance in {"long", "long_leaning"}) or (
        trade.get("direction") == "short" and stance in {"short", "short_leaning"}
    )
    opposed = (trade.get("direction") == "long" and stance in {"short", "short_leaning"}) or (
        trade.get("direction") == "short" and stance in {"long", "long_leaning"}
    )
    suffix = "포지션 방향 유지" if aligned else "포지션과 역행" if opposed else "판정 유보"
    return f"{label} · {suffix}"


def format_paper_event(event: dict[str, Any]) -> str:
    kind = str(event.get("kind") or "")
    if kind == "gate_diagnostic":
        funnel = _dump(event.get("funnel"))
        top = _dump(funnel.get("top_rejection"))
        return "\n".join(
            [
                "<b>🤖 엔진 페이퍼 · 게이트 과조임 의심</b>",
                f"최근 7일 평가 {int(funnel.get('evaluations') or 0)}회 · 진입 0회",
                f"최다 탈락: {escape(str(top.get('label') or '판정 근거 부족'))} ({int(top.get('count') or 0)}회)",
                "완화 제안은 자동 적용하지 않고 섀도 실험을 경유합니다.",
            ]
        )
    trade = _dump(event.get("trade"))
    direction = "롱" if trade.get("direction") == "long" else "숏"
    prefix = {
        "opened": "🤖 엔진 진입",
        "partial": "🤖 엔진 부분 익절",
        "closed": "🤖 엔진 청산",
    }.get(kind, "🤖 엔진 거래")
    price = trade.get("entry_price") if kind == "opened" else trade.get("exit_price") or trade.get("partial_exit_price")
    lines = [f"<b>{prefix} · {escape(str(trade.get('symbol') or '-'))} {direction} @ {_price(price)}</b>"]
    if kind == "opened":
        evidence = _dump(trade.get("entry_evidence")).get("items") or []
        labels = [_compact(str(_dump(item).get("claim") or _dump(item).get("label") or ""), 36) for item in evidence[:2]]
        lines.append(f"근거: {escape(' + '.join(item for item in labels if item) or '검증 게이트 통과')}")
    else:
        lines.append(f"net {_signed_pct(trade.get('net_return_pct'))} · 사유 {escape(_exit_reason(event.get('reason') or trade.get('exit_reason')))}")
    lines.append("실주문이 아닌 엔진 가상 거래 기록입니다.")
    return "\n".join(lines)


def format_status(payload: dict[str, Any]) -> str:
    jobs = payload.get("jobs", {})
    lines = ["<b>시스템 상태</b>"]
    alerts_24h = payload.get("alerts_24h") if isinstance(payload.get("alerts_24h"), dict) else None
    if alerts_24h:
        lines.append(f"알림 24h: 발화 {alerts_24h.get('fired', 0)} · 발송 {alerts_24h.get('delivered', 0)} · 실패 {alerts_24h.get('failed', 0)}")
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
        rows.append(
            f"├ 무효화 {_price(invalidation.get('price'))} ({_signed_pct(invalidation.get('distance_pct'))}) {escape(invalidation.get('action') or '조건 확인')}"
        )
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
    return "채점 가능한 구조 없음 — 데이터 표본 축적 중. 참조 존 형성 대기."


def _freshness(payload: dict[str, Any]) -> str:
    status = payload.get("insight_status") or {}
    if not status.get("has_insight"):
        return "인사이트 없음"
    if status.get("is_stale"):
        age = status.get("age_minutes")
        return f"과거 판단{f' {age}분 전' if age is not None else ''}"
    return "신선"


def _flow_line_from_position_payload(payload: dict[str, Any]) -> str:
    state = _state(payload)
    analysis = _dump(state.get("analysis"))
    derivatives = _dump(analysis.get("derivatives"))
    signals = _dump(derivatives.get("signals"))
    latest = _dump(derivatives.get("latest"))
    if not latest:
        return ""
    funding = _dump(signals.get("funding_state"))
    crowding = _dump(signals.get("crowding_score"))
    return (
        f"수급: OI 변화 {_nullable_pct(latest.get('open_interest_change_pct'))} · "
        f"펀딩 {escape(str(funding.get('label') or '표본 부족'))} · "
        f"쏠림 {_number(crowding.get('score'))}"
    )


def _money_flow_block_from_position_payload(payload: dict[str, Any]) -> str:
    state = _state(payload)
    analysis = _dump(state.get("analysis"))
    derivatives = _dump(analysis.get("derivatives"))
    signals = _dump(derivatives.get("signals"))
    money_flow = _dump(signals.get("money_flow"))
    if not money_flow:
        return ""

    flow_state = str(money_flow.get("state") or "mixed")
    narratives = {
        "spot_led": "가격 움직임에 현물 CVD 유입이 동반됐습니다. 선물만의 움직임보다 현물 참여가 확인된 구간입니다.",
        "futures_led": "가격 상승에 선물 매수와 OI 증가가 동반됐지만 현물 CVD 유입은 확인되지 않았습니다. 레버리지 주도 상승 여부를 확인할 구간이며 방향 확정 신호는 아닙니다.",
        "spot_absorb": "가격이 약세 또는 횡보인 동안 현물 CVD 유입이 관찰됐습니다. 매집 가능성을 감시하되 반전 확정으로 해석하지 않습니다.",
        "delever": "가격 하락과 OI 감소가 함께 나타났습니다. 신규 방향 베팅보다 기존 레버리지 정리가 진행되는 구간으로 봅니다.",
        "mixed": "현물·선물 체결 방향이 섞여 있어 자금 흐름만으로 방향을 확정할 수 없습니다.",
    }
    reason = str(money_flow.get("reason") or "").strip()
    if not money_flow.get("available") and reason:
        narrative = reason
    elif money_flow.get("provisional") and reason:
        narrative = reason
    else:
        narrative = narratives.get(flow_state, narratives["mixed"])
    source = escape(str(money_flow.get("source_label") or "Bitget 단일 거래소 프록시"))
    as_of = _time(money_flow.get("as_of"))
    label = escape(str(money_flow.get("label") or "자금 흐름 판정 유보"))
    return "\n".join(
        [
            f"<b>자금 흐름</b> · {label}",
            escape(narrative),
            f"출처 {source} · 기준 {as_of}",
        ]
    )


def _liquidity_line_from_payload(payload: dict[str, Any]) -> str:
    chart_analysis = _dump(payload.get("chart_analysis"))
    liquidity = _dump(chart_analysis.get("liquidity"))
    sweeps = liquidity.get("sweeps")
    if not isinstance(sweeps, list):
        return ""
    strong = next((item for item in sweeps if isinstance(item, dict) and item.get("confirmed") and item.get("grade") == "Strong"), None)
    if not strong:
        return ""
    side = "고점 스윕" if strong.get("side") == "buy_side" else "저점 스윕"
    price = _price(strong.get("price") or strong.get("pool_price"))
    confidence = strong.get("confidence")
    confidence_text = f" · 신뢰도 {confidence}" if confidence is not None else ""
    return f"유동성: {escape(side)} {price} 확정{escape(confidence_text)}"


def _briefing_line_from_payload(payload: dict[str, Any]) -> str:
    briefing = payload.get("analyst_briefing")
    if not isinstance(briefing, dict):
        return ""
    confluence = _dump(briefing.get("confluence"))
    stance = confluence.get("stance_label")
    score = confluence.get("composite_score")
    counter = confluence.get("counter_evidence")
    counter_count = len(counter) if isinstance(counter, list) else 0
    if not stance:
        return ""
    score_text = f" · 종합 {_number(score)}/100" if score is not None else ""
    return f"브리핑: {escape(str(stance))}{score_text} · 반대 근거 {counter_count}개"


def _one_liners_from_payload(payload_or_one_liners: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload_or_one_liners.get("lines"), list):
        return payload_or_one_liners
    chart_analysis = _dump(payload_or_one_liners.get("chart_analysis"))
    one_liners = chart_analysis.get("one_liners")
    if not isinstance(one_liners, dict):
        analysis = _dump(payload_or_one_liners.get("analysis"))
        one_liners = analysis.get("one_liners")
    return one_liners if isinstance(one_liners, dict) else {}


def _scout_confluence(payload: dict[str, Any]) -> dict[str, Any]:
    briefing = payload.get("analyst_briefing")
    if not isinstance(briefing, dict):
        return {}
    confluence = briefing.get("confluence")
    return confluence if isinstance(confluence, dict) else {}


def _scout_tilt_label(summary: dict[str, Any], one_liners: dict[str, Any], confluence: dict[str, Any]) -> str:
    confluence_stance = str(confluence.get("stance") or "")
    stance = {
        "long_leaning": "상방",
        "short_leaning": "하방",
        "conflicted": "충돌",
        "insufficient": "판단불가",
    }.get(confluence_stance, str(one_liners.get("overall_stance") or ""))
    stance_state = _dump(confluence.get("stance_state"))
    candles = int(stance_state.get("candles_in_state") or 0)
    held = f" 유지 · {candles}캔들째" if candles > 0 else ""
    if stance == "상방":
        return f"숏 ◀━━━●▶ 롱 (상방 우세{held})"
    if stance == "하방":
        return f"숏 ◀●━━━▶ 롱 (하방 우세{held})"
    if stance == "횡보":
        return "숏 ◀━━●━━▶ 롱 (중립)"
    if stance == "충돌":
        return "숏 ◀━━●━━▶ 롱 (가중 근거 충돌)"
    if stance == "판단불가":
        return "숏 ◀━━○━━▶ 롱 (근거 부족)"
    try:
        long_score = float(summary.get("long_score"))
        short_score = float(summary.get("short_score"))
        evidence = int(summary.get("long_evidence_count") or 0) + int(summary.get("short_evidence_count") or 0)
    except (TypeError, ValueError):
        return "숏 ◀━━○━━▶ 롱 (근거 부족)"
    if evidence < 3:
        return "숏 ◀━━○━━▶ 롱 (근거 부족)"
    diff = long_score - short_score
    if abs(diff) < 10:
        return "숏 ◀━━●━━▶ 롱 (충돌)"
    return "숏 ◀━━━●▶ 롱 (상방 근거 우세)" if diff > 0 else "숏 ◀●━━━▶ 롱 (하방 근거 우세)"


def _scout_decision_context(confluence: dict[str, Any]) -> list[str]:
    if not confluence:
        return []
    lines: list[str] = []
    long_score = confluence.get("long_score")
    short_score = confluence.get("short_score")
    if long_score is not None and short_score is not None:
        htf = _dump(confluence.get("htf_context"))
        htf_label = {
            "bearish": "하락",
            "bearish_to_neutral": "하락→중립",
            "neutral": "중립",
            "neutral_to_bullish": "중립→상승",
            "bullish": "상승",
        }.get(str(htf.get("htf_trend") or ""), "확인 불가")
        lines.append(f"가중 판정: 롱 {_number(long_score)} · 숏 {_number(short_score)} · 상위 추세 {escape(htf_label)}")
    state = _dump(confluence.get("stance_state"))
    if state.get("transitioning"):
        target = str(state.get("target") or state.get("pending_stance") or "")
        target_label = {
            "long_leaning": "상방",
            "short_leaning": "하방",
            "conflicted": "균형",
            "insufficient": "판단 유보",
        }.get(target, "방향 전환")
        try:
            progress = round(float(state.get("flip_threshold_progress") or 0) * 100)
        except (TypeError, ValueError):
            progress = 0
        lines.append(f"전환 관찰: 순간 {escape(target_label)} 시도 · 전환 문턱 {progress}%")
    return lines


def _scout_reason_line(summary: dict[str, Any]) -> str:
    reasons = [
        _dump(summary.get("top_event")).get("label"),
        _dump(summary.get("liquidity_nearest_pool")).get("label"),
        "반전 후보 구간" if summary.get("harmonic_active") else "",
        summary.get("funding_state"),
        summary.get("volume_state"),
    ]
    clean = []
    for item in reasons:
        text = _compact(str(item or "").strip(), 26)
        if text and text not in clean:
            clean.append(text)
    return " · ".join(clean[:2])


def _scout_trigger(summary: dict[str, Any]) -> str:
    if summary.get("entry_intent_distance_pct") is not None:
        return f"의도 {_nullable_pct(summary.get('entry_intent_distance_pct'))}"
    if summary.get("setup_proximity_pct") is not None:
        return _nullable_pct(summary.get("setup_proximity_pct"))
    if summary.get("liquidity_pool_distance_pct") is not None:
        return f"유동성 {_nullable_pct(summary.get('liquidity_pool_distance_pct'))}"
    if summary.get("nearest_level_distance_pct") is not None:
        return f"구조 {_nullable_pct(summary.get('nearest_level_distance_pct'))}"
    return ""


def _stance_dot(stance: str) -> str:
    return "○" if stance == "판단불가" else "●"


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


def _positive_distance(value: Any) -> str:
    try:
        return f"+{abs(float(value)):.2f}%"
    except (TypeError, ValueError):
        return "-"


def _signed_number(value: Any) -> str:
    try:
        return f"{float(value):+.2f}"
    except (TypeError, ValueError):
        return "-"


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _nullable_pct(value: Any) -> str:
    if value is None:
        return "표본 부족"
    return _signed_pct(value).replace("+", "")


def _ratio(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "표본 부족"


def _exit_reason(value: Any) -> str:
    return {
        "invalidation_breach": "무효화 이탈",
        "breakeven_stop": "본전 스탑",
        "opposite_stance_flip": "반대 스탠스 전환",
        "take_profit_pressure": "익절 압력 지속",
        "time_stop": "최대 보유시간",
        "time_decay": "스탠스 약화 시간종료",
        "take_profit_1": "1차 익절",
        "take_profit_2": "2차 익절",
    }.get(str(value or ""), str(value or "기록 없음"))


def _funding(value: Any) -> str:
    try:
        number = float(value) * 100
    except (TypeError, ValueError):
        return "-"
    return f"{number:+.4f}%"


def _number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    abs_number = abs(number)
    if abs_number >= 1_000_000_000:
        return f"{number / 1_000_000_000:.2f}B"
    if abs_number >= 1_000_000:
        return f"{number / 1_000_000:.2f}M"
    if abs_number >= 1_000:
        return f"{number / 1_000:.2f}K"
    if abs_number >= 1:
        return f"{number:.2f}"
    return f"{number:.6f}"


def _ratio_to_pct(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if 0 <= number <= 1:
        return f"{number * 100:.1f}%"
    return f"{number:.2f}"


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
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return escape(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(DISPLAY_TIMEZONE).strftime("%H:%M")


def _compact(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else f"{text[: max(0, limit - 1)]}…"
