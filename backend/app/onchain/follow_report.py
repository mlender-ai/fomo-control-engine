"""WO-FCE-REPORT-DEFECTS-01 7-2 — 추종 자격 조회의 **단일 출처**.

## 왜 이 모듈이 생겼나

리포트의 `대상 N지갑` 이 자격 통과 목록이 아니었다. 실측 2026-08-29:

```
🐋 대상  3지갑 · 0x10f1…202f · 0x9546…181c · 0x1ee7…edf5
```

`0x1ee7…edf5` 는 승률 점추정 51.3% 로 **자격 탈락 지갑**이다. 그런데 목록에 있었다.

원인은 출처였다. 리포트가 `whale_follow.performance_by_whale` 를 읽었고 그것은
**거래 이력이 있는 지갑**을 센다 — 자격과 무관하다. 게다가 그 안의 `win_pct` 는
`entry_evidence` 에 박힌 **진입 시점 스냅샷**이라 자격이 바뀌어도 따라오지 않는다.

> **자격 기준은 정확했다. 목록이 다른 것을 세고 있었다**(C4 — 기준 diff 0줄).

## 하나의 함수만 둔다

`runtime.whale_follow_eligibility()` 가 이미 같은 계산을 하고 있었지만 `runtime.repository`
전역에 묶여 있어 리포트 계층에서 부를 수 없었다. 그래서 **순수 함수로 꺼냈다** — 두 곳이
각자 계산하면 그것이 곧 두 개의 진실이고, `METRIC-TRUTH-01` 이 그것으로 한 번 깨졌다.

```
follow_report.follow_eligibility_report(repo)
    ├── runtime.whale_follow_eligibility()      (API · 워커)
    └── daily_report_source.build_report()      (텔레그램 리포트)
```

## 자격을 잃은 지갑의 열린 포지션 (7-2 항목 4)

**신규 진입만 막는다. 이미 연 포지션은 출구 규칙대로 닫는다.**

`whale_follow.run_entries` 는 `eligible` 을 받아 신호를 거르지만 `run_exits` 는 자격을
보지 않는다 — 열린 거래 전부를 판정한다. 그것이 옳다: 자격은 **진입 근거**이고 출구는
가격·시간이 정한다. 자격 상실을 이유로 임의 청산하면 그것은 새 출구 규칙이다(C1).

리포트는 그 상태를 **보이게만** 한다. 자격 밖인데 포지션이 열려 있으면 그 사실이 줄로 남는다.
"""

from __future__ import annotations

from typing import Any

from app.onchain import follow_eligibility


def follow_eligibility_report(repo: Any) -> dict[str, Any]:
    """추종 자격 판정 전체. **이 저장소에서 자격 목록의 유일한 출처다.**

    승격 기준(28일·N>=30·CI 하한 55%)을 읽지도 바꾸지도 않는다(C4).
    """
    from app.backtest.statistics import bootstrap_ci_from_counts
    from app.onchain import participant_type
    from app.onchain import service as onchain_service

    sizes = onchain_service.whale_sample_sizes(repo)
    wins = onchain_service.whale_sample_wins(repo)
    contaminated = onchain_service.contaminated_sample_addresses(repo)

    events: list[Any] = []
    for wallet in repo.list_whale_wallets(limit=1000):
        events.extend(repo.list_whale_events(wallet_address=wallet.address, limit=onchain_service.CLASSIFICATION_EVENTS_PER_WALLET))
    estimates = participant_type.classify_wallets(events)

    statuses: dict[str, follow_eligibility.FollowStatus] = {}
    for address, raw_size in sizes.items():
        excluded = int(contaminated.get(address, {}).get("excluded_sample") or 0)
        # 오염 지갑은 표본을 계수에서 뺀다 — 행은 지우지 않는다.
        size = 0 if excluded else int(raw_size)
        won = 0 if excluded else int(wins.get(address, 0))
        # CI 하한은 **표시 전용**이다. 자격 판정에 들어가지 않는다.
        ci = bootstrap_ci_from_counts(won, size) if size else None
        statuses[address] = follow_eligibility.follow_status(
            address=address,
            sample_size=size,
            wins=won,
            ci_low=round(float(ci[0]), 1) if ci else None,
            estimate=estimates.get(address),
            excluded_sample=excluded,
        )
    return {
        **follow_eligibility.summary(statuses),
        "statuses": {address: status.as_payload() for address, status in statuses.items()},
        "funnel": follow_eligibility.funnel(statuses),
        "contaminated": contaminated,
        "contaminated_sample_total": sum(int(item["excluded_sample"]) for item in contaminated.values()),
    }


def follow_targets(repo: Any, *, traded: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """리포트가 읽을 추종 대상 블록 (7-2).

    `traded` 는 `whale_follow.performance_by_whale` 결과다 — **거래 이력**이며 자격 목록이
    아니다. 두 집합을 나란히 내되 **어느 쪽이 자격인지 라벨이 말한다.**

    자격 밖인데 포지션이 열려 있는 지갑은 따로 센다. 신규 진입은 막히고 열린 포지션은
    출구 규칙대로 닫히므로, 그 수는 시간이 지나면 0 으로 간다 — 줄지 않으면 그것이 신호다.
    """
    report = follow_eligibility_report(repo)
    eligible = {str(address).lower() for address in report.get("eligible_addresses") or []}
    rows = traded or []
    lapsed = [row for row in rows if str(row.get("address") or "").lower() not in eligible and int(row.get("open") or 0) > 0]
    return {
        "eligible_addresses": sorted(eligible),
        "eligible_count": len(eligible),
        "passers": report.get("passers"),
        "funnel": report.get("funnel"),
        "zero_passers_note": report.get("zero_passers_note"),
        "traded_count": len(rows),
        "lapsed_with_open_positions": [str(row.get("address")) for row in lapsed],
        "source": "onchain.follow_eligibility.follow_status — 거래 이력이 아니다(7-2)",
        "lapsed_policy": "자격 상실은 **신규 진입만** 막는다. 열린 포지션은 출구 규칙대로 닫는다 — 임의 청산하지 않는다(C1).",
    }
