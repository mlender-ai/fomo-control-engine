"""WO-FCE-DIRECTIONAL-INTEGRITY-01 Phase 0 — 기준선 스냅샷 + 분포표 산출.

이후 모든 Phase 는 여기서 나온 스냅샷과 대조한다. **CI green 은 완료 조건이 아니다** —
기준선 대비 정량 대조표가 완료 조건이다(WO §13).

운영 DB 가 있는 호스트에서 돌린다:

```bash
cd backend
# ① 기준선 생성 (판정 로직 변경 전)
python3 scripts/directional_baseline_report.py ~/fomo_control_engine.db \
    --out docs/validation/baselines/directional_baseline.json

# ② Phase 적용 후 대조
python3 scripts/directional_baseline_report.py ~/fomo_control_engine.db \
    --compare docs/validation/baselines/directional_baseline.json
```

캔들은 `stance_history_candles` 에서 읽는다. 이 표는 `refresh_stance_backtests` 가
Bitget 공개 히스토리로 채우는 것이며, 비어 있으면 **추정하지 않고 그 사실을 보고한다.**
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.models import MarketCandle
from app.db.sqlite_utils import connect_sqlite
from app.validation import directional_replay as dr


def load_candles(connection: Any, symbol: str, timeframe: str, limit: int) -> list[MarketCandle]:
    rows = connection.execute(
        """
        SELECT opened_at, open, high, low, close, volume
        FROM stance_history_candles
        WHERE symbol = ? AND timeframe = ?
        ORDER BY opened_at DESC
        LIMIT ?
        """,
        (symbol.upper(), timeframe, int(limit)),
    ).fetchall()
    candles = [
        MarketCandle(
            timestamp=_parse(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5] or 0.0),
        )
        for row in rows
    ]
    return sorted(candles, key=lambda candle: candle.timestamp)


def available_symbols(connection: Any, timeframe: str) -> list[tuple[str, int]]:
    rows = connection.execute(
        "SELECT symbol, COUNT(*) FROM stance_history_candles WHERE timeframe = ? GROUP BY symbol ORDER BY COUNT(*) DESC",
        (timeframe,),
    ).fetchall()
    return [(str(row[0]), int(row[1])) for row in rows]


def _parse(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _table(rows: list[list[str]], header: list[str]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def render_markdown(snapshot: dict[str, Any]) -> str:
    summary = snapshot["summary"]
    coverage = summary["coverage"]
    silent = summary["silent_weight"]
    parts = [
        f"## {snapshot['symbol']} {snapshot['timeframe']} — 기준선 `{snapshot['records_sha256'][:12]}`",
        "",
        f"- 판정 지점 {summary['record_count']}건 · 구간 {summary['period']}",
        f"- {summary['sample_warning'] or '표본 30건 이상'}",
        "",
        "### 판정 가능 엔진 수 분포",
        "",
        _table(
            [[str(item["judged_engines"]), str(item["count"]), f"{item['pct']}%" if item["pct"] is not None else "-"] for item in coverage["buckets"]],
            ["판정 가능 엔진", "건수", "비율"],
        ),
        "",
        "### 침묵 가중 비율 분포",
        "",
        _table(
            [[item["range"], str(item["count"]), f"{item['pct']}%" if item["pct"] is not None else "-"] for item in silent["buckets"]],
            ["침묵 가중", "건수", "비율"],
        ),
        "",
        "### 엔진별 침묵 빈도",
        "",
        _table(
            [
                [item["engine"], str(item["base_weight"]), str(item["silent_count"]), f"{item['silent_pct']}%" if item["silent_pct"] is not None else "-"]
                for item in summary["engine_silence"]["engines"]
            ],
            ["엔진", "가중", "침묵 건수", "침묵 비율"],
        ),
        "",
        "### 와이코프 ↔ 유동성 불일치 (증상 B)",
        "",
        f"- 양쪽 다 방향을 낸 지점 {summary['wyckoff_liquidity']['both_directional']}건 중 **정반대 {summary['wyckoff_liquidity']['conflict_count']}건**"
        f" ({summary['wyckoff_liquidity']['conflict_pct_of_both_directional']}%)",
        f"- {summary['wyckoff_liquidity']['sample_warning'] or '표본 30건 이상'}",
        "",
        "### 선행 추세 대조 (D1 발생률)",
        "",
        f"- 인과 역전 **{summary['prior_trend']['conflict_count']}건** ({summary['prior_trend']['conflict_pct']}%)"
        f" — 마크다운 뒤 분산 {summary['prior_trend']['markdown_then_distribution']}건 ·"
        f" 마크업 뒤 축적 {summary['prior_trend']['markup_then_accumulation']}건",
        f"- ⚠️ {summary['prior_trend']['trend_window_caveat']}",
        "",
        "### 근거 항목 수 vs 엔진 수 (E2)",
        "",
        f"- 최소 근거를 충족한 {summary['evidence_vs_engine']['meets_item_threshold']}건 중"
        f" **단일 엔진 {summary['evidence_vs_engine']['single_engine_count']}건**"
        f" ({summary['evidence_vs_engine']['single_engine_pct_of_met']}%),"
        f" 2개 이하 {summary['evidence_vs_engine']['two_engines_or_fewer_count']}건",
        "",
        "### 채택 stance ↔ 순간 stance 분리 (E3)",
        "",
        f"- 분리 {summary['stance_split']['split_count']}건 ({summary['stance_split']['split_pct']}%)"
        f" · 그중 **정반대 {summary['stance_split']['opposed_count']}건** ({summary['stance_split']['opposed_pct']}%)",
        f"- {summary['stance_split']['note']}",
    ]
    return "\n".join(parts)


def render_comparison(comparison: dict[str, Any]) -> str:
    return "\n".join(
        [
            "## 기준선 대조",
            "",
            _table(
                [
                    ["기준선 해시", comparison["baseline_sha256"][:16]],
                    ["현행 해시", comparison["candidate_sha256"][:16]],
                    ["동일 여부", "예" if comparison["identical"] else "아니오"],
                    ["대조 지점", str(comparison["compared_points"])],
                    ["국면 변경", str(comparison["phase_changed"])],
                    ["stance 변경", str(comparison["stance_changed"])],
                    ["방향 판정 (기준선)", str(comparison["directional_baseline"])],
                    ["방향 판정 (현행)", str(comparison["directional_candidate"])],
                    ["증감", f"{comparison['directional_delta']:+d}"],
                    ["단조 감소 여부", "예" if comparison["monotone_decrease"] else "**아니오 — 방향 판정이 늘었다**"],
                ],
                ["항목", "값"],
            ),
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="방향 판정 재판정 기준선 산출")
    parser.add_argument("database", help="운영 SQLite 경로")
    parser.add_argument("--symbol", action="append", default=None, help="대상 심볼 (반복 가능). 생략 시 저장된 전 심볼")
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--limit", type=int, default=5_000, help="심볼당 최대 캔들 수")
    parser.add_argument("--out", default=None, help="기준선 JSON 저장 경로")
    parser.add_argument("--compare", default=None, help="대조할 기준선 JSON 경로")
    args = parser.parse_args()

    connection = connect_sqlite(args.database)
    try:
        stored = available_symbols(connection, args.timeframe)
        if not stored:
            print(f"stance_history_candles 에 {args.timeframe} 캔들이 없다. `refresh_stance_backtests` 가 아직 돌지 않았다는 뜻이며, 추정으로 채우지 않는다.")
            return 1
        symbols = [symbol.upper() for symbol in (args.symbol or [name for name, _count in stored])]
        snapshots: list[dict[str, Any]] = []
        for symbol in symbols:
            candles = load_candles(connection, symbol, args.timeframe, args.limit)
            records = dr.replay_judgments(symbol=symbol, timeframe=args.timeframe, candles=candles)
            if not records:
                print(f"{symbol}: 판정 지점 0건 — 캔들 {len(candles)}개로는 분석 창을 채우지 못한다.")
                continue
            snapshot = dr.baseline_snapshot(records, symbol=symbol, timeframe=args.timeframe)
            snapshots.append(snapshot)
            print(render_markdown(snapshot))
            print()
            if args.compare:
                baseline = json.loads(Path(args.compare).read_text(encoding="utf-8"))
                for item in baseline if isinstance(baseline, list) else [baseline]:
                    if str(item.get("symbol")) == symbol:
                        print(render_comparison(dr.compare_to_baseline(item, records)))
                        print()
        if args.out and snapshots:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(snapshots, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            print(f"기준선 저장: {args.out} ({len(snapshots)} 심볼)")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
