"""청산 경로 부재 구간의 고아 포지션을 성과 통계에서 제외한다 (WO-FCE-STOCK-EXIT-01 작업 4).

07-22(KR 3건)·07-23(US 3건) 진입분은 청산 경로가 존재하지 않던 구간의 산물이다. 8일 보유는
**엔진의 판단이 아니라 코드 부재의 결과**이므로, 어떤 방식으로 청산하든 그 손익은 엔진 성과가
아니다(C4). 따라서 통계에서 제외하되 원장 행은 그대로 남긴다.

소급 재현은 하지 않는다: 지금 작성한 청산 규칙을 과거 포지션에 적용하는 것은 라이브 페이퍼가
아니라 백테스트다. 이 스크립트는 **제외 표식만** 남기고, 실제 청산은 새 규칙 아래 정상 경로로
일어난다(그 체결의 손익도 통계에서는 빠진다).

실행: PYTHONPATH=. python3 scripts/void_orphan_stock_positions.py [--apply]
기본은 dry-run이며 --apply를 줘야 실제로 표식한다.
"""

from __future__ import annotations

import argparse

from app.core.config import get_settings
from app.stock_paper.models import Market
from app.stock_paper.service import VOID_NO_EXIT_PATH
from app.stock_paper.store import StockPaperStore


def main() -> None:
    parser = argparse.ArgumentParser(description="고아 주식 포지션을 통계에서 제외 표식")
    parser.add_argument("--apply", action="store_true", help="실제로 표식한다(미지정 시 dry-run)")
    args = parser.parse_args()

    store = StockPaperStore(get_settings().database_url)
    if not store.enabled:
        print("주식 페이퍼 저장소가 비활성입니다(memory:// 등). 처리할 것이 없습니다.")
        return

    positions = store.open_positions()
    # 청산 경로가 없던 구간의 포지션 = exit_plan이 없는 보유분. 새 진입은 체결 시 plan이 박힌다.
    orphans = [row for row in positions if not (row.get("exit_plan") or {}).get("invalidation_price")]

    print(f"보유 포지션 {len(positions)}건 · 고아(청산계획 없음) {len(orphans)}건")
    total_unrealized = 0.0
    for row in orphans:
        quantity = int(row["quantity"])
        average = float(row["average_price"])
        current = row.get("current_price")
        unrealized = (float(current) - average) * quantity if current is not None else None
        if unrealized is not None:
            total_unrealized += unrealized
        pnl_text = f"{unrealized:+,.2f}" if unrealized is not None else "미상(마크 없음)"
        print(f"  {row['market']} {row['symbol']:<10} 수량 {quantity:>6} · 평단 {average:>12,.2f} · 평가손익 {pnl_text}")
        if args.apply:
            store.mark_excluded_from_stats(Market(str(row["market"])), str(row["symbol"]), VOID_NO_EXIT_PATH)

    print(f"\n고아 평가손익 합계: {total_unrealized:+,.2f} (원통화 혼합 — 시장별로 따로 읽을 것)")
    if args.apply:
        print(f"→ {len(orphans)}건을 '{VOID_NO_EXIT_PATH}'로 통계 제외 표식했습니다.")
        print("   이 손익은 승률·평균 R·검증 표본에 포함되지 않습니다(C4).")
    else:
        print("→ dry-run입니다. 실제 표식하려면 --apply 를 주십시오.")


if __name__ == "__main__":
    main()
