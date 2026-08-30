"""WO-FCE-DAILY-REPORT-01 3-1 — 리포트 산출 계층.

지표를 만들지 않는다. **이미 있는 산출기를 읽어 리포트 형태로 모은다.**

조회 실패는 그 트랙만 `미상` 이 되고 리포트 전체를 막지 않는다 — 한 트랙의 DB 오류로
5트랙 리포트가 사라지면 그것이 더 나쁘다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from app.notify import daily_report

_EMPTY_METRICS: dict[str, Any] = {"trade_count": 0}


def _connect(database_url: str) -> sqlite3.Connection | None:
    path = database_url.removeprefix("sqlite:///") if database_url.startswith("sqlite:///") else ""
    if not path:
        return None
    try:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return connection
    except sqlite3.Error:
        return None


def _counts(connection: sqlite3.Connection, *, table: str, since: datetime) -> dict[str, int]:
    """직전 리포트 이후 진입·청산·승 건수. 창을 인자로 받는다 — 창 없는 수는 만들지 않는다."""
    stamp = since.isoformat()
    try:
        entries = connection.execute(f"SELECT COUNT(*) FROM {table} WHERE entry_bar_at >= ?", (stamp,)).fetchone()[0]
        rows = connection.execute(
            f"SELECT payload FROM {table} WHERE status='closed' AND exit_at IS NOT NULL AND exit_at >= ?",
            (stamp,),
        ).fetchall()
    except sqlite3.Error:
        return {"entries": 0, "exits": 0, "wins": 0}
    wins = 0
    for row in rows:
        try:
            import json

            if float((json.loads(row["payload"]) or {}).get("net_pnl_usdt") or 0.0) > 0:
                wins += 1
        except (TypeError, ValueError):
            continue
    return {"entries": int(entries), "exits": len(rows), "wins": wins}


def _stock_counts(connection: sqlite3.Connection, *, market: str, since: datetime) -> dict[str, int]:
    stamp = since.isoformat()
    try:
        buys = connection.execute("SELECT COUNT(*) FROM stock_paper_fills WHERE market=? AND side='buy' AND filled_at >= ?", (market, stamp)).fetchone()[0]
        sells = connection.execute("SELECT COUNT(*) FROM stock_paper_fills WHERE market=? AND side='sell' AND filled_at >= ?", (market, stamp)).fetchone()[0]
    except sqlite3.Error:
        return {"entries": 0, "exits": 0, "wins": 0}
    return {"entries": int(buys), "exits": int(sells), "wins": 0}


def _paper_metrics(repo: Any, settings: Any, track: str) -> dict[str, Any]:
    """승률·PF·MDD. `paper/service` 의 산출기를 그대로 쓴다(중복 구현 금지).

    크립토는 스코어보드가 이미 낸다. 추종 트랙은 같은 함수를 별도 원장에 적용한다 —
    지표 정의가 갈라지지 않게 한 함수만 쓴다.
    """
    from app.paper import service as paper_service

    try:
        if track == "crypto":
            board = paper_service.paper_scoreboard(repo, settings)
            return dict((board.get("competition") or {}).get("engine") or {}) or _EMPTY_METRICS
        if track == "whale_follow":
            trades = [item for item in repo.list_whale_follow_trades(limit=2000) if item.status == "closed"]
            if not trades:
                return _EMPTY_METRICS
            returns = [float(item.net_return_pct or 0.0) for item in trades]
            amounts = [float(item.net_pnl_usdt or 0.0) for item in trades]
            wins = sum(1 for value in amounts if value > 0)
            gross_profit = sum(value for value in amounts if value > 0)
            gross_loss = abs(sum(value for value in amounts if value < 0))
            return paper_service._metric_payload(
                returns,
                wins,
                gross_profit,
                gross_loss,
                scored_count=len(trades),
                neutral_count=0,
                pnl_usdt=amounts,
                capital_usdt=float(getattr(settings, "whale_follow_starting_capital_usdt", 0.0) or 0.0) or None,
                capital_note="임시값 · 크립토와 동일",
            )
    except Exception:
        return _EMPTY_METRICS
    return _EMPTY_METRICS


def _track_state(settings: Any, connection: sqlite3.Connection | None, track: str) -> dict[str, Any]:
    """정지·차단·보류 판정을 **읽는다**. 새로 판정하지 않는다(C8)."""
    if track == "poly":
        from app.validation import track_scope

        scope = track_scope.track_scope_status(track_scope.TRACK_POLY, settings)
        if scope["excluded"]:
            return {"kind": "excluded", "detail": "451 지역 차단 + 구조적 검증 불가"}
        return {}
    if track in {"stock_kr", "stock_us"} and connection is not None:
        market = "KR" if track == "stock_kr" else "US"
        try:
            row = connection.execute("SELECT status, stop_reason FROM stock_paper_tracks WHERE market=?", (market,)).fetchone()
        except sqlite3.Error:
            return {}
        if row and str(row["status"] or "") == "stopped":
            return {"kind": "halted", "detail": f"체결 invariant ({row['stop_reason']})"}
        if bool(getattr(settings, "stock_paper_hold_queued_orders", False)):
            try:
                held = connection.execute(
                    "SELECT COUNT(*) FROM stock_paper_orders WHERE status='queued' AND json_extract(payload,'$.market')=?",
                    (market,),
                ).fetchone()[0]
            except sqlite3.Error:
                held = 0
            if held:
                return {"kind": "held", "detail": f"봉 불일치 정지 예방 ({int(held):,}건)"}
    return {}


def _actions(settings: Any) -> list[dict[str, Any]]:
    """조치 필요 항목. **없으면 빈 목록**이고 그때 꼬리가 생략된다."""
    from app.validation import live_trading_gate, pending_decisions, provisional_defaults
    from app.worker import sleep_guard

    actions: list[dict[str, Any]] = []
    try:
        from app.stock_paper.routes import _host_persistence_warning

        warning = _host_persistence_warning(settings)
    except Exception:
        warning = {}
    if warning.get("blocking"):
        ceilings = " · ".join(f"{market} {row['effective_day_ceiling']}일" for market, row in (warning.get("ceilings") or {}).items())
        actions.append(
            {
                "title": f"호스트 절전 — 유효일 상한 {ceilings} (28일 창 미달)",
                "command": warning.get("command"),
            }
        )
    try:
        items = pending_decisions.pending_decisions(
            gate_approved=live_trading_gate.GATE_APPROVED,
            sleep_guard=sleep_guard.sleep_guard_status(),
        )
        blocking = [item for item in items if item.get("severity") == pending_decisions.BLOCKING]
        provisional = [item for item in items if item.get("severity") == pending_decisions.PROVISIONAL]
        applied = provisional_defaults.applied_defaults(settings)
    except Exception:
        return actions
    if blocking or applied:
        detail_parts = []
        if provisional:
            detail_parts.append(f"결정 전이 {len(provisional)}건")
        if applied:
            detail_parts.append(f"임시값 {len(applied)}건 적용 중")
        actions.append(
            {
                "title": f"미결 결정 {len(blocking)}건",
                "detail": " · ".join(detail_parts) or None,
            }
        )
    return actions


def build_report(repo: Any, settings: Any, *, now: datetime, last_sent_at: datetime | None = None) -> dict[str, Any]:
    """5트랙 리포트. 조회 실패는 그 트랙만 미상이 된다."""
    from app.paper import whale_exit_replay, whale_follow
    from app.validation import track_capital

    since = daily_report.window_start(last_sent_at, now=now)
    connection = _connect(str(getattr(settings, "database_url", "") or ""))
    capitals = track_capital.all_tracks(connection, settings)["tracks"] if connection is not None else {}

    tracks: dict[str, dict[str, Any]] = {}
    totals = {"entries": 0, "exits": 0}
    for track, _label in daily_report.TRACK_ORDER:
        capital = capitals.get(track) or {"currency": track_capital.TRACK_CURRENCY.get(track, "?")}
        if connection is None:
            counts = {"entries": 0, "exits": 0, "wins": 0}
        elif track == "crypto":
            counts = _counts(connection, table="paper_trades", since=since)
        elif track == "whale_follow":
            counts = _counts(connection, table="whale_follow_trades", since=since)
        elif track in {"stock_kr", "stock_us"}:
            counts = _stock_counts(connection, market="KR" if track == "stock_kr" else "US", since=since)
        else:
            counts = {"entries": 0, "exits": 0, "wins": 0}
        extra: list[str] = []
        if track == "whale_follow":
            # 7-2 — `대상` 은 **자격 통과 목록**이다. 거래 이력이 아니다.
            #
            # 이전에는 `performance_by_whale`(거래 이력)을 세어 자격 탈락 지갑
            # (`0x1ee7…edf5` · 승률 51.3%)이 `대상` 으로 찍혔다. 자격 기준은 정확했고
            # 목록이 다른 것을 세고 있었다(C4 — 기준 diff 0줄).
            try:
                from app.onchain import follow_report

                traded = whale_follow.performance_by_whale(repo.list_whale_follow_trades(limit=500))
                targets = follow_report.follow_targets(repo, traded=traded)
                passers = targets.get("passers") or []
                if passers:
                    short = " · ".join(f"{row['address'][:6]}…{row['address'][-4:]}" for row in passers[:3])
                    extra.append(f"  대상  {targets['eligible_count']}지갑 자격 통과 · {short}")
                else:
                    extra.append("  대상  0지갑 — 자격 통과 없음 (기준을 낮추지 않는다)")
                lapsed = targets.get("lapsed_with_open_positions") or []
                if lapsed:
                    # 자격을 잃었는데 포지션이 열려 있다. 신규 진입은 막히고 출구는 규칙대로다(C1·C6).
                    extra.append(f"  보유  {len(lapsed)}지갑 자격 상실 · 신규 진입 차단 · 출구 규칙대로 청산")
                funnel = targets.get("funnel") or {}
                if funnel.get("population"):
                    extra.append(f"  자격  {funnel['label']}")
            except Exception:
                extra.append("  대상  미상 — 자격 조회 실패")
            # 2-4: 출구 반사실 한 줄. "얼마 잃었다"만 오면 **출구를 의심할 계기가 없다.**
            # B 는 실적이 아니라 대조군이며 라벨에 못 박는다(C2·C11). 길이 제한 안에서 한 줄만.
            try:
                comparison = whale_exit_replay.build_comparison(repo, settings)
                summary = comparison.get("overall") or {}
                delta = summary.get("delta_net")
                if summary.get("count") and delta is not None:
                    sign = "+" if delta >= 0 else ""
                    caution = "" if comparison.get("verdict", {}).get("actionable") else " · 전환 근거 아님"
                    extra.append(f"  출구  고래청산 추종 시 {sign}{delta:.2f} (반사실 {summary['count']}건){caution}")
                # 2-8 항목 3 — **한 항목만.** 갭이 얼마이고 표본이 판정 가능한가.
                # 가설 셋을 본문에 다 실으면 길이 한도를 먹는다 — 그것은 화면 몫이다.
                gap = comparison.get("gap") or {}
                if gap.get("gap_pp") is not None:
                    verdict = "판정 가능" if gap.get("actionable") else "표본 부족 · 판정 보류"
                    extra.append(f"  갭    고래 {gap['whale_win_pct']}% vs 추종 {gap['follow_win_pct']}% ({gap['gap_pp']}%p) · {verdict}")
            except Exception:
                pass
        if track in {"stock_kr", "stock_us"} and connection is not None:
            # 전략(엄격) 표본만 센다. 탐색 계정은 성적에 합산하지 않는다(원 WO C4).
            market = "KR" if track == "stock_kr" else "US"
            try:
                strict = connection.execute("SELECT COUNT(*) FROM stock_paper_fills WHERE market=? AND entry_mode='strict_signal'", (market,)).fetchone()[0]
                coverage = connection.execute("SELECT COUNT(*) FROM stock_paper_fills WHERE market=? AND entry_mode='coverage'", (market,)).fetchone()[0]
            except sqlite3.Error:
                strict = coverage = 0
            extra.append(f"  표본  전략 {int(strict)} · 탐색 {int(coverage)} (탐색은 성적에 합산하지 않음)")
        tracks[track] = {
            "capital": capital,
            "counts": counts,
            "metrics": _paper_metrics(repo, settings, track),
            "state": _track_state(settings, connection, track),
            "extra": extra,
        }
        totals["entries"] += int(counts["entries"])
        totals["exits"] += int(counts["exits"])
    if connection is not None:
        connection.close()
    return {
        "as_of": now.isoformat(),
        "as_of_label": daily_report.kst_label(now),
        "window_start": since.isoformat(),
        "tracks": tracks,
        "totals": totals,
        "actions": _actions(settings),
        # C5 — 총합 줄을 만들지 않는다. 통화가 다르고 판정이 독립이다.
        "no_total": "트랙 간 자본·손익을 합산하지 않는다",
    }
