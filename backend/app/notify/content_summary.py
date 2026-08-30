"""일일 요약의 내용물 — 진입 · 폴리마켓 · 리더보드 검증.

## 왜 이 모듈이 생겼나

사용자 지시(2026-08-16):

> "하트비트 죽는거 그만 보고해. 살아있다는것도 보고하지마. 그게 아무런 도움이 안 돼.
>  그냥 포지션을 들어갔냐, 폴리마켓은, 리더보드는 잘 검증되고 있는지만 요약해서 보내라고.
>  죽은건 보내봤자 내가 그걸 보고 어떠한 인사이트도 얻을 수가 없잖아."

타당한 지적이다. 생존 신호는 **행동을 바꾸지 못한다** — 죽었다는 알림을 받아도 사용자가 할 수
있는 일이 없고(supervisor 가 이미 자동 재시작한다), 살아있다는 알림은 정보가 0이다.
실측: `heartbeat_stale` 156회 발송 중 사용자 조치로 이어진 것은 없다.

그래서 요약은 **읽고 판단할 수 있는 것만** 담는다.

## 정직성 규칙 (기존 계약 승계)

- 표본이 30 미만이면 **N 을 병기하고 "표본 부족"을 명시**한다. 승률만 크게 쓰지 않는다.
- 구조적으로 표본이 생길 수 없는 트랙은 그 사실을 **매번** 적는다 — 조용히 0으로 두면
  "아직 안 나왔나 보다"로 읽힌다.
- 인과를 단정하지 않는다. 관측만 적는다.
- 데이터가 없으면 "미산출 + 사유"다. 지어내지 않는다.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

# 이 미만이면 승률을 성적으로 발표하지 않는다.
MIN_SAMPLE = 30


def _connect(database_url: str) -> sqlite3.Connection | None:
    path = database_url.removeprefix("sqlite:///") if database_url.startswith("sqlite:///") else ""
    if not path:
        return None
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def poly_line(connection: sqlite3.Connection, *, now: datetime, window_days: int = 28) -> str:
    """폴리마켓 — 정산이 검증 창 안에 도달하는가.

    보유 건수만 적으면 "잘 굴러가고 있다"로 읽힌다. 실제로 판정을 좌우하는 것은
    **검증 창 안에 만기가 오는 건수**이고, 그것이 0이면 몇 건을 들고 있든 표본은 0이다.
    """
    deadline = (now + timedelta(days=window_days)).isoformat()
    try:
        open_count = connection.execute("SELECT COUNT(*) FROM poly_positions WHERE status='open'").fetchone()[0]
        resolved = connection.execute("SELECT COUNT(*) FROM poly_positions WHERE status='resolved'").fetchone()[0]
        due = connection.execute(
            """SELECT COUNT(*) FROM poly_positions p JOIN poly_markets m ON m.market_id=p.market_id
            WHERE p.status='open' AND m.end_at IS NOT NULL AND m.end_at <= ?""",
            (deadline,),
        ).fetchone()[0]
        nearest = connection.execute(
            """SELECT MIN(m.end_at) FROM poly_positions p JOIN poly_markets m ON m.market_id=p.market_id
            WHERE p.status='open' AND m.end_at IS NOT NULL""",
        ).fetchone()[0]
    except sqlite3.Error as exc:
        return f"🎲 <b>폴리마켓</b> — 미산출 (조회 실패: {str(exc)[:40]})"

    head = f"🎲 <b>폴리마켓</b> — 보유 {open_count}건 · 정산 완료 {resolved}건"
    if due > 0:
        return f"{head}\n  {window_days}일 내 정산 예정 {due}건 · 최근접 만기 {str(nearest)[:10]}"
    # 0이면 그 사실이 핵심이다. 매번 적는다.
    tail = f"최근접 만기 {str(nearest)[:10]}" if nearest else "만기 정보 없음"
    return f"{head}\n  ⚠️ {window_days}일 내 정산 예정 <b>0건</b> ({tail}) — 이 창에서는 표본이 생기지 않습니다"


def _selection_basis(connection: sqlite3.Connection) -> str:
    """발견 캐시에 기록된 실제 선정 기준. 없으면 미상이라고 쓴다 — 추측하지 않는다."""
    try:
        row = connection.execute("SELECT payload FROM calibration_report_cache WHERE report_key='hyperliquid_leaderboard_discovery'").fetchone()
    except sqlite3.Error:
        return "미상"
    if row is None:
        return "미상"
    try:
        payload = json.loads(str(row["payload"]))
        basis = ((payload or {}).get("payload") or payload or {}).get("selection_basis")
    except (TypeError, ValueError):
        return "미상"
    return str(basis) if basis else "미상"


def whale_line(connection: sqlite3.Connection, *, min_sample: int = MIN_SAMPLE) -> str:
    """리더보드(고래) — 사후 채점이 쌓이고 있는가.

    선정은 `quality_score`(PnL·ROI·규모)이고 승률은 **사후 채점**이다. 이 구분을 매번 적는다 —
    승률을 앞에 세우면 "승률로 뽑았다"로 오해된다.
    """
    try:
        from app.onchain.win_rate import observed_win_rates, selection_disclosure

        events = [dict(r) for r in connection.execute("SELECT wallet_address, event_type, payload FROM whale_events")]
        wallets = connection.execute("SELECT COUNT(*) FROM whale_wallets WHERE active=1").fetchone()[0]
    except Exception as exc:
        return f"🐋 <b>리더보드</b> — 미산출 (조회 실패: {str(exc)[:40]})"

    # **같은 `min_sample` 을 계산과 라벨 양쪽에 넘긴다** (WO-FCE-REPORT-DEFECTS-01 7-3).
    #
    # 넘기지 않았을 때 `observed_win_rates` 는 자기 기본값 20 을 쓰는데 라벨은 여기 30 을
    # 찍었다. 그래서 `표본 30건 이상 지갑 65개` 가 실제로는 **20건 이상**이었다 — 라벨이
    # 거짓이었고, 그 65 를 자격 통과 3 과 나란히 두면 읽는 사람이 감소로 오해한다.
    rates = observed_win_rates(events, min_sample=min_sample)
    disclosure = selection_disclosure(rates, min_sample=min_sample)
    scored = disclosure["scored_wallets"]
    total = disclosure["closed_samples"]
    overall = disclosure["overall_win_rate_pct"]
    median = disclosure["wallet_median_win_rate_pct"]

    # "추종 지갑"이 아니라 **추적군**이다. 추종 자격은 별개이며 실측 20 대 1 이다 —
    # 깔때기 라벨이 같은 혼동을 일으켰고(`WHALE-COHORT-COLLAPSE-01`) 여기도 같았다.
    head = f"🐋 <b>리더보드</b> — 추적군 {wallets}개 · 사후 채점 {total:,}건"
    if not total:
        return f"{head}\n  아직 청산 표본이 없습니다 — 승률 미산출"
    # 7-3 항목 1·4 — 계산 방식을 값 옆에 박는다. 체결 가중과 지갑 중앙값은 다른 수다.
    body = f"  사후 승률 {overall}% (체결 가중 · 전체 지갑)"
    if median is not None:
        body += f" · 지갑 중앙값 {median}% (표본 {min_sample}건 이상 {scored}개)"
    else:
        body += f" · 표본 {min_sample}건 이상 지갑 {scored}개"
    if scored == 0:
        body += " · ⚠️ 표본 부족 — 판정 불가"
    # 이 승률은 **고래 자신의 체결 손익**이다. 추종 자격의 승률(우리 판정 원장 채점)과
    # 모집단도 계산도 다르다 — 두 수를 나란히 두고 "감소"라고 부르면 안 된다(7-3 항목 2).
    body += "\n  ※ 고래 자신의 체결 승률 — 추종 자격 승률(우리 판정 채점)과 다른 모집단"
    # 선정 기준을 하드코딩하지 않는다. `WHALE-FOLLOW-01` 6-1 이 비성과(규모·활동량)로
    # 바꿨는데 이 문구는 `quality_score(PnL·ROI·규모)` 로 남아 있었다 — 거짓이다(C10).
    return f"{head}\n{body}\n  선정 기준: {_selection_basis(connection)} · 승률은 사후 채점"


def entry_line(connection: sqlite3.Connection, *, now: datetime, hours: int = 24) -> str:
    """진입·청산 — 어제 하루 실제로 무엇을 했는가.

    "진입 0건"도 반드시 적는다. 줄이 없으면 "알림이 안 온 건가"와 구분되지 않는다.
    """
    since = (now - timedelta(hours=hours)).isoformat()
    try:
        crypto_open = connection.execute("SELECT COUNT(*) FROM paper_trades WHERE entry_bar_at >= ?", (since,)).fetchone()[0]
        crypto_close = connection.execute("SELECT COUNT(*) FROM paper_trades WHERE exit_at >= ?", (since,)).fetchone()[0]
        held = connection.execute("SELECT COUNT(*) FROM paper_trades WHERE status='open'").fetchone()[0]
        stock = [
            dict(r) for r in connection.execute("SELECT market, side, COUNT(*) n FROM stock_paper_fills WHERE filled_at >= ? GROUP BY market, side", (since,))
        ]
    except sqlite3.Error as exc:
        return f"📌 <b>진입·청산 ({hours}시간)</b> — 미산출 (조회 실패: {str(exc)[:40]})"

    lines = [f"📌 <b>진입·청산 (최근 {hours}시간)</b>"]
    lines.append(f"  크립토: 진입 {crypto_open}건 · 청산 {crypto_close}건 · 보유 {held}건")
    if stock:
        parts = " · ".join(f"{r['market']} {'매수' if r['side'] == 'buy' else '매도'} {r['n']}건" for r in stock)
        lines.append(f"  주식: {parts}")
    else:
        lines.append("  주식: 체결 0건")
    return "\n".join(lines)


def content_summary_lines(database_url: str, *, now: datetime | None = None) -> list[str]:
    """요약 본문. 조회 실패는 그 줄만 '미산출'로 남기고 요약 전체를 막지 않는다."""
    now = now or datetime.now(timezone.utc)
    connection = _connect(database_url)
    if connection is None:
        return []
    try:
        return ["", entry_line(connection, now=now), "", poly_line(connection, now=now), "", whale_line(connection)]
    finally:
        connection.close()
