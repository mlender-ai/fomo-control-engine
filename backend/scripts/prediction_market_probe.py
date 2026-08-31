"""WO-FCE-MAKE-IT-RUN-01 Phase 2 — 예측시장 공급원 탐침. **호스트에서 실행한다.**

## 왜 이 스크립트가 필요한가

`451` 은 **지역 차단**이므로 어디서 부르는지가 결과를 바꾼다. 개발 컨테이너에서 잰 값은
호스트의 접근 가능성을 말해주지 않는다 — 실제로 이 저장소의 개발 컨테이너는 이그레스
허용목록 때문에 폴리·칼시 양쪽 모두 **연결 자체가 안 된다**(`000`). 그것을 `451` 로 적으면
거짓이 된다.

> **그래서 판정은 호스트에서 이 스크립트를 돌려야 나온다.**

## 우회하지 않는다 (C3)

프록시·VPN 을 쓰지 않는다. 공개 엔드포인트를 그대로 호출하고 **상태 코드를 그대로 적는다.**
`451` 이 나오면 `451` 이라고 적는 것이 이 스크립트의 일이다.

## 만기 분포가 핵심이다

폴리가 `STRUCTURALLY_BLOCKED` 였던 이유는 차단이 아니라 **만기가 2027-01-01** 이라 검증
창(28일) 안에 정산이 없었던 것이다. 대체 공급원을 만기 확인 없이 채택하면 **같은 결론이
반복된다**(금지 조항).

그래서 접근이 되는 소스마다 **28일 안에 만기가 오는 시장이 몇 개인지** 센다. 0 이면
접근 가능해도 채택할 수 없다.

## 실행

```bash
cd backend && PYTHONPATH=. python3 scripts/prediction_market_probe.py
```
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

# 검증 창. `COMPLETION_DEFINITION.md` 의 28일과 같은 수다 — 여기서 새로 정하지 않는다.
VALIDATION_WINDOW_DAYS = 28
TIMEOUT_SECONDS = 20

# 공개 읽기 전용 엔드포인트만. 인증·서명이 필요한 경로는 넣지 않는다(봉인 · C7).
ENDPOINTS: tuple[tuple[str, str, str], ...] = (
    ("polymarket.gamma", "https://gamma-api.polymarket.com/markets?limit=50&closed=false", "end_date_iso"),
    ("polymarket.clob", "https://clob.polymarket.com/sampling-markets", "end_date_iso"),
    ("polymarket.data", "https://data-api.polymarket.com/trades?limit=1", ""),
    ("kalshi", "https://api.elections.kalshi.com/trade-api/v2/markets?limit=50&status=open", "close_time"),
)


def probe(url: str) -> dict[str, Any]:
    """상태 코드를 **있는 그대로** 돌려준다. 실패를 성공처럼 적지 않는다."""
    request = urllib.request.Request(url, headers={"User-Agent": "fce-probe/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return {"status": int(response.status), "body": response.read(400_000).decode("utf-8", "replace")}
    except urllib.error.HTTPError as exc:
        # 451·403 은 여기로 온다. **이것이 우리가 재려는 값이다.**
        return {"status": int(exc.code), "body": exc.read(2000).decode("utf-8", "replace"), "reason": exc.reason}
    except Exception as exc:
        # 연결 자체가 안 된 것. 지역 차단과 **다른 사건**이므로 구분해 적는다.
        return {"status": None, "error": f"{type(exc).__name__}: {exc}", "note": "연결 실패 — 지역 차단(451)과 다르다"}


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def expiry_distribution(body: str, field: str, *, now: datetime) -> dict[str, Any]:
    """28일 창 안에 만기가 오는 시장 수. **0 이면 접근 가능해도 채택 불가다.**"""
    if not field:
        return {"applicable": False, "note": "만기 필드가 없는 엔드포인트다"}
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return {"applicable": False, "note": "JSON 이 아니다"}
    rows = payload if isinstance(payload, list) else (payload.get("markets") or payload.get("data") or [])
    horizon = now + timedelta(days=VALIDATION_WINDOW_DAYS)
    within = 0
    beyond = 0
    unknown = 0
    for row in rows if isinstance(rows, list) else []:
        stamp = _parse(row.get(field)) if isinstance(row, dict) else None
        if stamp is None:
            unknown += 1
        elif stamp <= horizon:
            within += 1
        else:
            beyond += 1
    return {
        "applicable": True,
        "sampled": len(rows) if isinstance(rows, list) else 0,
        "within_window": within,
        "beyond_window": beyond,
        "unknown_expiry": unknown,
        "window_days": VALIDATION_WINDOW_DAYS,
        "adoptable": within > 0,
        "note": "0 이면 접근 가능해도 채택할 수 없다 — 폴리가 STRUCTURALLY_BLOCKED 였던 이유가 만기였다",
    }


def main() -> int:
    now = datetime.now(timezone.utc)
    results = []
    for name, url, field in ENDPOINTS:
        outcome = probe(url)
        row: dict[str, Any] = {"source": name, "url": url, "status": outcome.get("status")}
        if outcome.get("error"):
            row["error"] = outcome["error"]
            row["note"] = outcome["note"]
        elif outcome.get("status") == 200:
            row["expiry"] = expiry_distribution(outcome.get("body") or "", field, now=now)
        else:
            row["body_head"] = (outcome.get("body") or "")[:200]
        results.append(row)
        print(json.dumps(row, ensure_ascii=False, indent=2))

    reachable = [row for row in results if row.get("status") == 200]
    adoptable = [row for row in reachable if (row.get("expiry") or {}).get("adoptable")]
    print("\n=== 판정 근거 ===")
    print(f"접근 가능 소스 : {len(reachable)}/{len(results)}")
    print(f"채택 가능 소스 : {len(adoptable)} (28일 창 안에 만기가 오는 시장이 존재)")
    if not reachable:
        print("→ 접근 가능한 공급원이 없다. **트랙 종료**를 검토한다(Phase 2 항목 4).")
    elif not adoptable:
        print("→ 접근은 되지만 28일 창 안에 만기가 없다. 채택하면 STRUCTURALLY_BLOCKED 가 반복된다.")
    else:
        print(f"→ 채택 후보: {', '.join(row['source'] for row in adoptable)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
