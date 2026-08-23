"""WO-FCE-ASSET-CLASS-01 3-2·3-3 — 자산군 오분류 감사 (읽기 전용).

## 이 모듈은 분류를 바꾸지 않는다

**계측만 한다.** 분류 수리 자체는 C1 이 막고 있다 — 3-1(라이브 타임아웃 종결)이 닫히기
전에는 심볼을 늘리지 않는다. 이 모듈은 그 결정을 내리는 데 필요한 숫자를 낸다:
**무엇이 몇 종 바뀌고, 바뀌면 어떤 게이트가 새로 걸리는가.**

## 두 경로가 갈리고 부정확한 쪽이 이긴다 (D2)

```python
scout/universe.py:68
asset_class = str(summary.get("asset_class")        # ← 이름 기반. 먼저 평가되어 이긴다
                  or analysis.get("asset_class")    # ← 이름 기반
                  or item.get("asset_class")        # ← 카탈로그(정확). 여기까지 오지 않는다
                  or "unknown")
```

`summary` 와 `analysis` 는 둘 다 `classify_asset_class(symbol)` 를 **메타데이터 없이**
호출한다(`positions/chart_analysis.py:73` · `services/scout_handlers.py:747`). 메타데이터가
없으면 `isRwa` 를 볼 수 없으므로 27개짜리 `STOCK_TICKERS` 허용목록으로 떨어진다.

카탈로그는 `list_contracts()` 의 `raw_metadata` 를 그대로 넘겨 분류하므로 `isRwa=YES` 를
본다. **같은 함수인데 입력이 달라 결과가 갈린다** — 버그는 함수가 아니라 호출부에 있다.

## 분류를 고치면 무엇이 새로 걸리는가 (D3 — 표시가 아니라 리스크다)

`crypto → stock` 으로 옮겨가는 심볼은 게이트 두 개를 **새로 받는다**:

| 게이트 | 위치 | 지금(crypto) | 수리 후(stock) |
| --- | --- | --- | --- |
| `stage2_template` | `scout/universe.py:239` | 건너뜀 | 적용 |
| `earnings_clear` | `paper/policy.py:173` | 항상 통과 | **항상 불통과** ← §참고 |

> ⚠️ **`earnings_clear` 는 "제대로 걸리게" 되는 것이 아니라 "영구 차단"이 된다.**
> `paper/service.py::_earnings_clear` 는 `analysis["earnings"]` 를 읽는데 크립토 분석
> 경로에는 그 키를 채우는 코드가 **없다**. 그래서 stock/index 는 `bool({}) == False` 로
> 무조건 불통과다. 실측은 `audit_earnings_gate_inputs()` 가 낸다.
>
> 즉 분류만 고치면 262종의 진입이 0이 된다 — `DISCOVERY-UNBLOCK-01` 이 겪은
> "표본을 늘리려는 변경이 표본을 0으로 만들었다"와 **같은 형태**다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from app.marketdata.assets import STOCK_TICKERS, base_ticker, classify_asset_class


# 분류가 바뀌면 새로 적용되는 게이트. **값은 건드리지 않는다**(C2) — 적용 대상만 바뀐다.
GATES_GAINED_BY_STOCK = ("stage2_template", "earnings_clear")

# 세션 필터가 stock·index 에서만 떨어내는 비율 (CANDLE_SUPPLY.md §0 실측).
SESSION_FILTER_LOSS_PCT = 30.0

# `stage2_template` 이 요구하는 캔들 수. 대조용으로 읽기만 한다(C2).
STAGE2_MIN_CANDLES = 200


@dataclass(frozen=True)
class ClassificationDiff:
    """한 심볼에서 두 경로가 갈린 결과."""

    symbol: str
    catalog_class: str
    name_based_class: str
    is_rwa: bool
    ticker: str
    in_stock_allowlist: bool

    @property
    def changed(self) -> bool:
        return self.catalog_class != self.name_based_class

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "catalog_class": self.catalog_class,
            "name_based_class": self.name_based_class,
            "changed": self.changed,
            "is_rwa": self.is_rwa,
            "ticker": self.ticker,
            "in_stock_allowlist": self.in_stock_allowlist,
            "gates_gained": list(GATES_GAINED_BY_STOCK) if self.changed and self.catalog_class in {"stock", "index"} else [],
        }


def classification_diffs(catalog: Iterable[Any]) -> list[ClassificationDiff]:
    """카탈로그 각 항목에서 **카탈로그 분류 vs 이름 기반 분류**를 나란히 낸다.

    카탈로그가 정확한 이유는 `raw_metadata` 를 넘겨 `isRwa` 를 보기 때문이고, 이름 기반이
    틀리는 이유는 그것을 못 보기 때문이다. 여기서 새로 분류하지 않는다 — **같은 함수를 두
    입력으로 호출**해 차이를 드러낼 뿐이다.
    """
    rows: list[ClassificationDiff] = []
    for item in catalog:
        symbol = str(getattr(item, "symbol", "") or "").upper()
        if not symbol:
            continue
        metadata = getattr(item, "raw_metadata", None)
        metadata = metadata if isinstance(metadata, dict) else {}
        ticker = base_ticker(symbol, str(getattr(item, "base_coin", "") or ""))
        rows.append(
            ClassificationDiff(
                symbol=symbol,
                catalog_class=str(getattr(item, "asset_class", "") or "unknown"),
                # 라이브가 실제로 쓰는 경로를 그대로 재현한다 — 메타데이터를 주지 않는다.
                name_based_class=classify_asset_class(symbol),
                is_rwa=str(metadata.get("isRwa", "")).upper() == "YES",
                ticker=ticker,
                in_stock_allowlist=ticker in STOCK_TICKERS,
            )
        )
    return sorted(rows, key=lambda row: row.symbol)


def misclassification_summary(diffs: Sequence[ClassificationDiff]) -> dict[str, Any]:
    """오분류 규모 (3-2 작업 4). **몇 종이 어디서 어디로 옮겨가는가.**"""
    changed = [row for row in diffs if row.changed]
    transitions: dict[str, int] = {}
    for row in changed:
        transitions[f"{row.name_based_class}→{row.catalog_class}"] = transitions.get(f"{row.name_based_class}→{row.catalog_class}", 0) + 1
    catalog_counts: dict[str, int] = {}
    name_counts: dict[str, int] = {}
    for row in diffs:
        catalog_counts[row.catalog_class] = catalog_counts.get(row.catalog_class, 0) + 1
        name_counts[row.name_based_class] = name_counts.get(row.name_based_class, 0) + 1
    return {
        "total_symbols": len(diffs),
        "changed": len(changed),
        "rwa_symbols": sum(1 for row in diffs if row.is_rwa),
        "stock_allowlist_size": len(STOCK_TICKERS),
        "catalog_counts": dict(sorted(catalog_counts.items())),
        "name_based_counts": dict(sorted(name_counts.items())),
        "transitions": dict(sorted(transitions.items())),
        "symbols": [row.as_dict() for row in changed],
        "note": "카탈로그가 정본이다. 이름 기반은 27개 허용목록이라 RWA 대부분을 crypto 로 본다.",
    }


def audit_earnings_gate_inputs(earnings_clear: Any) -> dict[str, Any]:
    """`earnings_clear` 가 자산군별로 무엇을 돌려주는지 **실행해서** 확인한다 (3-3).

    코드를 읽고 추론하지 않는다 — 라이브가 쓰는 그 함수를 그대로 호출한다. 인자로 받는
    이유는 이 모듈이 `paper/` 를 임포트해 순환을 만들지 않기 위해서다.

    반환의 `permanently_blocked` 가 참이면 **그 자산군은 실적 데이터가 없어 진입이 원천
    차단**된다. 분류 수리로 심볼이 그 자산군에 들어가는 순간 진입이 0이 된다.
    """
    rows = {}
    for asset_class in ("crypto", "stock", "index"):
        # 실적 데이터가 전혀 없는 상태 = 라이브 크립토 분석 경로의 현실.
        without_data = bool(earnings_clear({"asset_class": asset_class}))
        # 데이터가 있고 이벤트 창 밖인 상태 = 정상 통과 조건.
        with_clear_data = bool(earnings_clear({"asset_class": asset_class, "earnings": {"blocked": False, "days_to_event": 30}}))
        rows[asset_class] = {
            "clear_without_earnings_data": without_data,
            "clear_with_earnings_data": with_clear_data,
            "permanently_blocked": not without_data and with_clear_data,
        }
    blocked = sorted(name for name, row in rows.items() if row["permanently_blocked"])
    return {
        "by_asset_class": rows,
        "permanently_blocked_classes": blocked,
        "feed_present": not blocked,
        "note": ("`analysis['earnings']` 를 채우는 코드가 크립토 분석 경로에 없다. 데이터가 있으면 통과하지만 공급원이 없어 stock·index 는 항상 불통과다."),
    }


def reclassification_impact(
    diffs: Sequence[ClassificationDiff],
    *,
    earnings_audit: dict[str, Any],
    session_filter_loss_pct: float = SESSION_FILTER_LOSS_PCT,
) -> dict[str, Any]:
    """분류를 고치면 실제로 무슨 일이 일어나는가 (3-2·3-3·3-4 종합).

    **이 함수는 권고하지 않는다.** 결과만 낸다 — 켤지 말지는 3-1 예산과 실적 공급원 결정을
    본 뒤 사람이 정한다.
    """
    moving_to_gated = [row for row in diffs if row.changed and row.catalog_class in {"stock", "index"}]
    blocked_classes = set(earnings_audit.get("permanently_blocked_classes") or [])
    would_be_blocked = [row for row in moving_to_gated if row.catalog_class in blocked_classes]
    keep_ratio = max(0.0, 1.0 - session_filter_loss_pct / 100.0)
    return {
        "symbols_gaining_gates": len(moving_to_gated),
        "gates_gained": list(GATES_GAINED_BY_STOCK),
        # 3-3: 실적 게이트에 걸려 **진입이 0이 되는** 심볼 수. 리스크 결함이지 표시 결함이 아니다.
        "symbols_blocked_by_missing_earnings_feed": len(would_be_blocked),
        "earnings_feed_present": bool(earnings_audit.get("feed_present")),
        # 3-4: 세션 필터를 새로 받는 심볼. 200봉을 채우려면 공급이 얼마나 필요한가.
        "symbols_gaining_session_filter": len(moving_to_gated),
        "session_filter_loss_pct": session_filter_loss_pct,
        "required_supply_for_stage2": int(-(-STAGE2_MIN_CANDLES // keep_ratio)) if keep_ratio > 0 else None,
        "verdict": _verdict(len(moving_to_gated), len(would_be_blocked)),
    }


def _verdict(gaining: int, blocked: int) -> str:
    if gaining == 0:
        return "분류 변경 대상 없음 — 카탈로그와 이름 기반이 일치한다."
    if blocked:
        return (
            f"분류만 고치면 {blocked}종의 진입이 **0이 된다** — 실적 데이터 공급원이 없어 "
            "`earnings_clear` 가 영구 불통과이기 때문이다. 공급원 또는 무데이터 정책을 먼저 정해야 한다."
        )
    return f"{gaining}종이 게이트를 새로 받는다. 3-1 예산 한계 안에서 단계 확대할 것."
