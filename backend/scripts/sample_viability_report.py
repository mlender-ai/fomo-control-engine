"""WO-FCE-SAMPLE-VIABILITY-01 — 표본 생성 능력 실측 리포트.

`docs/validation/SAMPLE_VIABILITY.md` 의 표를 **실측으로** 채우는 도구다. 운영 DB 가 있는
호스트에서 돌린다:

```bash
cd backend && python3 scripts/sample_viability_report.py ~/fomo_control_engine.db
```

추정 계수를 만들지 않는다. 실적이 0인 트랙은 0으로 적고, 관측 창이 짧아 비율이 불안정하면
신뢰구간을 함께 낸다. 출력은 그대로 문서에 붙일 수 있는 마크다운이다.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.db.sqlite_utils import connect_sqlite
from app.poly_paper.parameters import load_poly_parameters
from app.validation import sample_viability as sv
from app.worker import market_calendar, observation


def _table(rows: list[list[str]], header: list[str]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def _phase1(connection, now: datetime) -> str:
    report = sv.sample_viability_report(connection, now=now)
    tracks = report["tracks"]
    order = ["crypto", "stock_kr", "stock_us", "poly"]
    labels = [tracks[key]["label"] for key in order]

    def _row(title: str, getter) -> list[str]:
        return [title, *[str(getter(tracks[key])) for key in order]]

    rows = [
        _row("유효 관측일", lambda t: f"{t['effective_days']}/{t['effective_days_target']}"),
        _row(
            "유효 관측일당 진입 (실측)",
            lambda t: f"{t['entries_per_effective_day']} (95% CI {t['entries_per_effective_day_ci95'][0]}~{t['entries_per_effective_day_ci95'][1]})",
        ),
        _row("진입당 청산 완료율 (실측)", lambda t: f"{round(t['exit_completion_rate'] * 100, 1)}% ({t['scored_samples']}/{t['entries_total']})"),
        _row("D+28 채점 가능 표본 예상", lambda t: str(t["projected_samples_at_target"])),
        _row("N≥30 달성 가능", lambda t: "가능" if t["reaches_target"] else "불가"),
        _row(
            "N≥30 예상 시점",
            lambda t: f"유효 {t['effective_days_to_target']}일 후 (실현 속도 환산 {t['calendar_days_to_target']}일)" if t["effective_days_to_target"] else "—",
        ),
        _row("판정", lambda t: t["verdict"]),
    ]
    out = ["## PHASE 1 — 트랙별 표본 산출 실측", "", _table(rows, ["항목", *labels]), ""]
    out.append("### 판정 근거")
    for key in order:
        track = tracks[key]
        out.append(f"- **{track['label']}** — `{track['verdict']}`: {track['verdict_reason']}")
        for note in track["notes"]:
            if note:
                out.append(f"  - {note}")
    out += ["", "### 진술 (규칙 1·2)"]
    out += [f"- {tracks[key]['statement']}" for key in order]
    return "\n".join(out)


def _phase2(connection, now: datetime) -> str:
    block = sv.poly_structural_block(connection, now=now)
    out = ["## PHASE 2 — 폴리 만기 편향 진단", "", f"- 구조적 차단: **{block['blocked']}** — {block['reason']}", f"- 상세: {block.get('detail')}"]
    try:
        rows = connection.execute(
            """SELECT m.end_at, m.liquidity, m.trade_eligible, e.gross_edge, e.after_cost_edge
            FROM poly_markets m LEFT JOIN poly_estimates e ON e.id=(
                SELECT id FROM poly_estimates WHERE market_id=m.market_id ORDER BY observed_at DESC LIMIT 1)
            WHERE m.closed=0"""
        ).fetchall()
    except Exception as exc:  # 폴리 테이블이 없는 DB 에서도 리포트 전체가 죽지 않게 한다
        return "\n".join([*out, f"- 만기 분포 산출 실패: {exc}"])
    buckets: dict[str, list] = {"<=7d": [], "8-28d": [], "29-90d": [], ">90d": [], "unknown": []}
    for row in rows:
        end = row["end_at"]
        if not end:
            buckets["unknown"].append(row)
            continue
        parsed = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        parsed = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        days = (parsed - now).total_seconds() / 86_400
        buckets["<=7d" if days <= 7 else "8-28d" if days <= 28 else "29-90d" if days <= 90 else ">90d"].append(row)

    def _median(values: list[float]) -> str:
        ordered = sorted(values)
        return f"{ordered[len(ordered) // 2]:.4f}" if ordered else "—"

    table = [
        [
            key,
            str(len(group)),
            _median([float(row["liquidity"] or 0) for row in group]),
            _median([float(row["gross_edge"]) for row in group if row["gross_edge"] is not None]),
            _median([float(row["after_cost_edge"]) for row in group if row["after_cost_edge"] is not None]),
            f"{round(sum(int(row['trade_eligible'] or 0) for row in group) / len(group) * 100, 1)}%" if group else "—",
        ]
        for key, group in buckets.items()
    ]
    out += [
        "",
        "만기 구간별 선정 재료 — **선정 점수에 만기 항은 없다.** 편향이 있다면 아래 항을 통한 간접 경로다.",
        "",
        _table(table, ["만기 구간", "N", "유동성 중앙값", "총엣지 중앙값", "비용후엣지 중앙값", "통과율"]),
        "",
        "> N 이 한 자리인 구간의 중앙값 차이는 근거가 되지 않는다.",
        "",
        _settlement_latency(connection),
    ]
    return "\n".join(out)


def _settlement_latency(connection) -> str:
    """정산 지연 실측 — `settlement_buffer_days` 의 근거 (WO-FCE-VALIDATION-VERDICT-01 Phase 5).

    만기 당일에 정산이 확정되지 않는다. 버퍼를 추측으로 두지 않기 위해 실측을 같이 낸다.
    """
    try:
        rows = connection.execute(
            """SELECT r.resolved_at, m.end_at FROM poly_resolutions r
            JOIN poly_markets m ON m.market_id=r.market_id WHERE m.end_at IS NOT NULL"""
        ).fetchall()
    except Exception as exc:
        return f"### 정산 지연 실측\n\n- 산출 실패: {exc}"
    deltas = []
    for row in rows:
        try:
            resolved = datetime.fromisoformat(str(row["resolved_at"]).replace("Z", "+00:00"))
            end = datetime.fromisoformat(str(row["end_at"]).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        deltas.append((resolved - end).total_seconds() / 86_400)
    parameters = load_poly_parameters()
    lines = ["### 정산 지연 실측 — `settlement_buffer_days` 근거", "", f"- 현재 설정값: **{parameters.settlement_buffer_days}일**"]
    if not deltas:
        lines.append("- 실측 표본 **0건** — 재측정 불가. 현재 값은 보수적 추정이며 정산이 쌓이면 다시 잰다.")
        return "\n".join(lines)
    ordered = sorted(deltas)
    median = ordered[len(ordered) // 2]
    lines += [
        f"- 실측 N={len(ordered)} · 중앙값 {median:.3f}일 · 최대 {ordered[-1]:.3f}일",
        f"- 표본 충분(N≥30): {'예' if len(ordered) >= 30 else '**아니오 — 판정 유보**'}",
        "- N<30 이면 중앙값을 그대로 쓰지 않고 보수적으로 올려 잡는다.",
    ]
    return "\n".join(lines)


def _phase3(connection, now: datetime, days: int) -> str:
    out = ["## PHASE 3 — 주식 KR 커버리지 축 분해", ""]
    window = [now.date() - timedelta(days=offset) for offset in range(days, 0, -1)]
    lost_days = []
    for day in window:
        row = observation.daily_coverage(connection, "stock_kr", day, now=now)
        if row["trading_day"] == 1 and row["valid"] == 0:
            lost_days.append(day)
    audit = market_calendar.holiday_audit("KR", lost_days)
    out += [
        "### 3-2 첫 항목 — 휴장일 오계상 여부 (먼저 확인)",
        "",
        f"- 유실일 {len(lost_days)}일 검사 → **{audit['verdict']}**",
        f"- 확정 휴장일이 유실로 잡힌 날: {audit['confirmed_holidays_in_list'] or '없음'}",
        f"- 확인 필요한 휴장 후보: {[item['day'] + ' ' + item['reason'] for item in audit['pending_holiday_candidates']] or '없음'}",
        "",
    ]
    breakdown = observation.axis_breakdown(connection, "stock_kr", window, now=now)
    out += [
        "### 축 분해",
        "",
        _table(
            [
                ["휴장일", breakdown["holiday"]["verdict"]],
                ["호스트 절전", breakdown["host_sleep"]["verdict"]],
                ["장 시간 경계", breakdown["session_edge"]["verdict"]],
                ["소스 응답", breakdown["source_response"]["verdict"]],
            ],
            ["축", "판정"],
        ),
        "",
        f"- 세션 경계 편중일: {breakdown['session_edge']['edge_dominant_days'] or '없음'}",
        f"- 트랙 고유 실패일(호스트는 생존): {breakdown['source_response']['track_specific_failure_days'] or '없음'}",
    ]
    return "\n".join(out)


def _phase6(connection, now: datetime) -> str:
    report = sv.sample_viability_report(connection, now=now)
    out = ["## PHASE 6 — 검증 완료 현황 (3조건)", "", "```"]
    out += [report["completion"][track]["line"] for track in ["crypto", "poly", "stock_kr", "stock_us"]]
    out += ["```", "", "세 조건을 **모두** 만족해야 완료다. 가장 뒤처진 조건이 그 트랙의 상태다."]
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="트랙별 표본 생성 능력 실측 리포트 (WO-FCE-SAMPLE-VIABILITY-01).")
    parser.add_argument("database", type=Path, help="운영 SQLite 경로")
    parser.add_argument("--days", type=int, default=30, help="축 분해 대상 기간(일)")
    parser.add_argument("--json", action="store_true", help="마크다운 대신 기계 판독본 출력")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    with connect_sqlite(str(args.database)) as connection:
        if args.json:
            print(json.dumps(sv.sample_viability_report(connection, now=now), ensure_ascii=False, indent=2))
            return
        sections = [
            f"<!-- 실측 {now.isoformat()} · DB {args.database} -->",
            _phase1(connection, now),
            _phase2(connection, now),
            _phase3(connection, now, args.days),
            _phase6(connection, now),
        ]
    print("\n\n".join(sections))


if __name__ == "__main__":
    main()
