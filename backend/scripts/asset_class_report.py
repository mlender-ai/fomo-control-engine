"""WO-FCE-ASSET-CLASS-01 3-2·3-3·3-4 — 자산군 오분류 감사 (읽기 전용).

**분류를 바꾸지 않는다. DB 에 쓰지 않는다.** 3-1(라이브 타임아웃 종결)이 닫히기 전에
심볼을 늘리지 않는다는 C1 을 지키면서, 그 결정에 필요한 숫자를 낸다.

```bash
cd backend
PYTHONPATH=. python3 scripts/asset_class_report.py --database ~/fomo_control_engine.db
PYTHONPATH=. python3 scripts/asset_class_report.py --database ~/fomo_control_engine.db \
    --out docs/validation/baselines/asset_class_before.json
```

카탈로그가 비어 있으면 **추정하지 않고 그 사실을 보고하고 종료한다** —
`refresh_symbol_catalog` 잡이 먼저 돌아야 한다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.db.repository import create_repository
from app.marketdata import asset_class_audit as aca
from app.paper.service import _earnings_clear


def render(summary: dict[str, Any], earnings: dict[str, Any], impact: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("## 1. 분류 실태 (3-2 작업 4)\n")
    lines.append(f"- 카탈로그 심볼: **{summary['total_symbols']:,}** · RWA(`isRwa=YES`): **{summary['rwa_symbols']:,}**")
    lines.append(f"- 이름 기반 허용목록 크기: **{summary['stock_allowlist_size']}** (`STOCK_TICKERS`)")
    lines.append(f"- 두 경로가 갈리는 심볼: **{summary['changed']:,}**\n")
    lines.append("| 경로 | crypto | stock | index | unknown |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    for label, counts in (("카탈로그(정확)", summary["catalog_counts"]), ("이름 기반(라이브)", summary["name_based_counts"])):
        lines.append(f"| {label} | {counts.get('crypto', 0)} | {counts.get('stock', 0)} | {counts.get('index', 0)} | {counts.get('unknown', 0)} |")
    if summary["transitions"]:
        lines.append("\n이동:")
        for transition, count in summary["transitions"].items():
            lines.append(f"- `{transition}` **{count}종**")

    lines.append("\n## 2. `earnings_clear` 실측 (3-3 · 리스크)\n")
    lines.append("| 자산군 | 실적 데이터 없음 | 실적 데이터 있음(창 밖) | 영구 차단 |")
    lines.append("| --- | --- | --- | --- |")
    for asset_class, row in earnings["by_asset_class"].items():
        lines.append(
            f"| {asset_class} | {'통과' if row['clear_without_earnings_data'] else '**불통과**'} | "
            f"{'통과' if row['clear_with_earnings_data'] else '불통과'} | "
            f"{'**예**' if row['permanently_blocked'] else '아니오'} |"
        )
    lines.append(f"\n> {earnings['note']}")

    lines.append("\n## 3. 분류 수리의 영향 (3-2·3-3·3-4 종합)\n")
    lines.append(f"- 게이트를 새로 받는 심볼: **{impact['symbols_gaining_gates']:,}** ({', '.join(impact['gates_gained'])})")
    lines.append(f"- **실적 공급원 부재로 진입이 0이 되는 심볼: {impact['symbols_blocked_by_missing_earnings_feed']:,}**")
    lines.append(f"- 세션 필터를 새로 받는 심볼: **{impact['symbols_gaining_session_filter']:,}** (손실 {impact['session_filter_loss_pct']}%)")
    lines.append(f"- `stage2_template` 200봉을 채우려면 공급 **{impact['required_supply_for_stage2']}봉** 필요")
    lines.append(f"\n> **판정**: {impact['verdict']}")

    if summary["symbols"]:
        lines.append("\n## 4. 분류 변경 심볼 목록\n")
        lines.append("| 심볼 | 이름 기반 | 카탈로그 | isRwa | 허용목록 | 새 게이트 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for row in summary["symbols"][:400]:
            lines.append(
                f"| {row['symbol']} | {row['name_based_class']} | **{row['catalog_class']}** | "
                f"{'YES' if row['is_rwa'] else 'no'} | {'있음' if row['in_stock_allowlist'] else '없음'} | "
                f"{', '.join(row['gates_gained']) or '—'} |"
            )
        if len(summary["symbols"]) > 400:
            lines.append(f"\n… 외 {len(summary['symbols']) - 400}종")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="자산군 오분류 감사 (읽기 전용)")
    parser.add_argument("--database", default=None, help="운영 SQLite 경로 (기본: settings.database_url)")
    parser.add_argument("--out", default=None, help="결과 JSON 저장 경로")
    args = parser.parse_args()

    settings = get_settings()
    database = args.database or settings.database_url.removeprefix("sqlite:///")
    repo = create_repository(f"sqlite:///{database}")

    catalog = repo.search_symbols("", limit=10_000)
    if not catalog:
        print("symbol_catalog 가 비어 있다. `refresh_symbol_catalog` 잡이 먼저 돌아야 한다 — 추정으로 채우지 않는다.")
        return 1

    diffs = aca.classification_diffs(catalog)
    summary = aca.misclassification_summary(diffs)
    earnings = aca.audit_earnings_gate_inputs(_earnings_clear)
    impact = aca.reclassification_impact(diffs, earnings_audit=earnings)

    print("=" * 100)
    print(f"WO-FCE-ASSET-CLASS-01 자산군 감사 — {database}")
    print("읽기 전용 · 분류를 바꾸지 않는다 (C1: 3-1 종결 전 3-2 착수 금지)")
    print("=" * 100)
    print()
    print(render(summary, earnings, impact))

    if args.out:
        payload = {"summary": summary, "earnings": earnings, "impact": impact, "read_only": True}
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\n저장: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
