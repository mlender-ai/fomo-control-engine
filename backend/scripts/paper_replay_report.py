"""WO-FCE-REPLAY-DEPTH-01 4-4 — 페이퍼 재판정 CLI (읽기 전용).

저장된 `stance_history_candles` 전 구간에 대해 페이퍼 엔진을 재실행한다. **DB 에 쓰지
않는다.**

```bash
cd backend
# ① 전 구간 재판정 + 손절 체결 반사실 (봉 중간 터치 vs 종가)
PYTHONPATH=. python3 scripts/paper_replay_report.py --database ~/fomo_control_engine.db

# ② 파라미터 스윕 — 축을 하나씩 가른다
PYTHONPATH=. python3 scripts/paper_replay_report.py --database ~/fomo_control_engine.db --sweep

# ③ 발표값 자동 대조 (원장 기준선까지 포함). 어긋나면 종료 코드 1
PYTHONPATH=. python3 scripts/paper_replay_report.py --database ~/fomo_control_engine.db --compare-published
```

⚠️ **여기서 나오는 숫자는 전부 반사실이다** (C9). 라이브 성적으로 인용하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.db.models import MarketCandle
from app.db.sqlite_utils import connect_sqlite
from app.paper.policy import PaperPolicy
from app.paper.service import policy_from_settings
from app.validation import paper_replay as pr
from app.validation import published_values as pv
from app.validation import risk_sizing_replay as rsr


# 스윕 축. **한 번에 하나씩** — 여러 축을 동시에 움직인 행만 보면 무엇이 효과였는지
# 영원히 알 수 없다(AGENTS.md).
SWEEP_AXES: tuple[tuple[str, dict[str, Any]], ...] = (
    ("현행", {}),
    ("잠금 same_bar", {"reentry_lock_mode": "same_bar"}),
    ("잠금 1봉", {"reentry_lock_mode": "bars", "reentry_lock_bars": 1}),
    ("리스크예산 1.5", {"sizing_mode": "risk_based", "risk_budget_usdt": 1.5}),
    ("리스크예산 2.5", {"sizing_mode": "risk_based", "risk_budget_usdt": 2.5}),
    ("리스크예산 4.0", {"sizing_mode": "risk_based", "risk_budget_usdt": 4.0}),
    ("TP2 배수 1.5", {"take_profit_atr_k2": 1.5}),
    ("TP2 배수 2.5", {"take_profit_atr_k2": 2.5}),
    ("RR 기준 net", {"rr_basis": "net"}),
)


def load_candles(connection: Any, symbol: str, timeframe: str, limit: int) -> list[MarketCandle]:
    rows = connection.execute(
        """SELECT opened_at, open, high, low, close, volume
           FROM stance_history_candles
           WHERE symbol = ? AND timeframe = ?
           ORDER BY opened_at DESC LIMIT ?""",
        (symbol.upper(), timeframe, int(limit)),
    ).fetchall()
    from datetime import datetime

    candles = [
        MarketCandle(
            timestamp=datetime.fromisoformat(str(row[0])),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5] or 0.0),
        )
        for row in rows
    ]
    return sorted(candles, key=lambda candle: candle.timestamp)


def stored_symbols(connection: Any, timeframe: str) -> list[tuple[str, int]]:
    rows = connection.execute(
        "SELECT symbol, COUNT(*) FROM stance_history_candles WHERE timeframe = ? GROUP BY symbol ORDER BY COUNT(*) DESC",
        (timeframe,),
    ).fetchall()
    return [(str(row[0]), int(row[1])) for row in rows]


def _metrics_row(label: str, metrics: dict[str, Any]) -> str:
    profit_factor = "n/a" if metrics["profit_factor"] is None else f"{metrics['profit_factor']:.4f}"
    return (
        f"{label:<24} N={metrics['sample_size']:>3}  gross={metrics['gross_r']:+8.3f}R  "
        f"비용={metrics['cost_r']:7.3f}R  net={metrics['net_r']:+8.3f}R  "
        f"PF={profit_factor:>7}  MDD={metrics['mdd_usdt']:7.2f}  판정={metrics['judgment_points']:>5}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="페이퍼 재판정 (읽기 전용 · 반사실)")
    parser.add_argument("--database", default=None, help="운영 SQLite 경로 (기본: settings.database_url)")
    parser.add_argument("--symbol", action="append", default=None, help="대상 심볼 (반복 가능). 생략 시 저장된 전 심볼")
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--limit", type=int, default=5_000, help="심볼당 최대 캔들 수")
    parser.add_argument("--sweep", action="store_true", help="파라미터 스윕 실행")
    parser.add_argument("--compare-published", action="store_true", help="발표값 대조 — 어긋나면 종료 코드 1")
    parser.add_argument("--out", default=None, help="결과 JSON 저장 경로")
    args = parser.parse_args()

    settings = get_settings()
    database = args.database or settings.database_url.removeprefix("sqlite:///")
    policy = policy_from_settings(settings, "crypto")

    connection = connect_sqlite(database)
    try:
        stored = stored_symbols(connection, args.timeframe)
        if not stored:
            print(
                f"stance_history_candles 에 {args.timeframe} 캔들이 없다. `replay_history_backfill` 잡이 아직 돌지 않았다는 뜻이며(기본값 꺼짐), 추정으로 채우지 않는다."
            )
            return 1
        symbols = [symbol.upper() for symbol in (args.symbol or [name for name, _bars in stored])]

        print("=" * 118)
        print(f"WO-FCE-REPLAY-DEPTH-01 4-4 페이퍼 재판정 — {database}")
        print(f"정책 {policy.version} · stance={policy.stance_gate_mode} · signature={policy.signature_gate_mode} · sizing={policy.sizing_mode}")
        print("⚠️ 전부 반사실이다 — 라이브 실적이 아니다(C9).")
        print("=" * 118)

        payload: dict[str, Any] = {"kind": pr.REPLAY_KIND, "database": database, "timeframe": args.timeframe, "symbols": {}}
        total_points = 0
        total_trades = 0
        for symbol in symbols:
            candles = load_candles(connection, symbol, args.timeframe, args.limit)
            if len(candles) < 120:
                print(f"\n{symbol}: 캔들 {len(candles)}개 — 분석 창을 채우지 못한다. 건너뛴다.")
                continue
            counterfactual = pr.stop_execution_counterfactual(
                symbol=symbol,
                timeframe=args.timeframe,
                candles=candles,
                policy=policy,
            )
            print(f"\n## {symbol} — 캔들 {len(candles):,}봉\n")
            print(_metrics_row("현행(종가 손절)", counterfactual["close"]))
            print(_metrics_row("반사실(봉 중간 터치)", counterfactual["intrabar"]))
            delta = counterfactual["delta"]
            print(f"{'차이':<24} N={delta['sample_size']:+3}  gross={delta['gross_r']:+8.3f}R  net={delta['net_r']:+8.3f}R  MDD={delta['mdd_usdt']:+7.2f}")
            print(f"{'손절 건수':<24} 종가 {counterfactual['stop_counts']['close']} · 봉중간 {counterfactual['stop_counts']['intrabar']}")
            payload["symbols"][symbol] = counterfactual
            total_points += int(counterfactual["close"]["judgment_points"])
            total_trades += int(counterfactual["close"]["trades_closed"])

            if args.sweep:
                sweep = pr.sweep(
                    symbol=symbol,
                    timeframe=args.timeframe,
                    candles=candles,
                    variants=pr.policy_variants(policy, list(SWEEP_AXES)),
                )
                print(f"\n### {symbol} 파라미터 스윕 (한 번에 한 축)\n")
                for row in sweep["rows"]:
                    print(_metrics_row(row["policy"], row))
                print(f"\n> {sweep['overfit_warning']}")
                payload["symbols"][symbol]["sweep"] = sweep

        payload["totals"] = {
            "judgment_points": total_points,
            "trades_closed": total_trades,
            "live_judgments_per_day": 12,
            "live_equivalent_days": round(total_points / 12, 1) if total_points else 0.0,
        }
        print("\n## 표본 규모 (C9 — 표본 수이지 실적이 아니다)\n")
        print(f"- 재판정 판정 지점 **{total_points:,}건** · 청산 표본 **{total_trades}건**")
        print(f"- 라이브 하루 12건 기준 **{payload['totals']['live_equivalent_days']}일치**")

        if args.compare_published:
            measured = {
                "stop_execution.phase2": _phase2_measured(database),
            }
            summary = pv.compare_all({key: value for key, value in measured.items() if value})
            payload["published_comparison"] = summary
            print("\n## 발표값 자동 대조 (4-4 작업 4)\n")
            for result in summary["compared"]:
                status = "일치" if result["status"] == "ok" else "**불일치**"
                print(f"- `{result['key']}` — {status} ({result['source_doc']})")
                for failure in result["failures"]:
                    print(f"    - {failure}")
            for item in summary["skipped"]:
                print(f"- `{item['key']}` — 건너뜀 ({item['reason']})")
            if summary["failures"]:
                print("\n발표값이 재현되지 않는다. 문서를 고치거나 원인을 찾기 전에는 새 숫자를 발표하지 않는다.", file=sys.stderr)
                return 1

        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            print(f"\n저장: {args.out}")
        return 0
    finally:
        connection.close()


def _phase2_measured(database: str) -> dict[str, Any] | None:
    """`STOP_EXECUTION.md` §1 이 발표한 손절 8건 평균을 원장에서 다시 낸다."""
    trades = rsr.load_trades(database)
    rows = rsr.stop_executions(trades)
    if not rows:
        return None
    gross = [item.trade.gross_r for item in rows]
    cost = [item.trade.cost_r for item in rows]
    net = [item.trade.net_r for item in rows]
    return {
        "n": len(rows),
        "mean_gross_r": round(sum(gross) / len(gross), 3),
        "mean_cost_r": round(sum(cost) / len(cost), 3),
        "mean_net_r": round(sum(net) / len(net), 3),
    }


def default_policy() -> PaperPolicy:
    """스윕 기준 정책. 라이브가 쓰는 것과 같은 경로로 만든다."""
    return policy_from_settings(get_settings(), "crypto")


if __name__ == "__main__":
    raise SystemExit(main())
