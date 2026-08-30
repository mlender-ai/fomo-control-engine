"""WO-FCE-TRACK-CAPITAL-01 — 4트랙 자본 원장.

## 왜 이 모듈이 생겼나

화면 어디에도 **트랙별 시작 자본 → 현재 자본**이 없었다. 폴리는 `USDC 8,416.88` 만 보였고
그것이 10,000 에서 줄어든 것인지 5,000 에서 는 것인지 화면으로 알 수 없었다.

`METRIC-TRUTH-01` 이 크립토에서 고친 결함(퍼센트 단순합을 대표값으로 쓰는 것)이 나머지 세
트랙에 그대로 남아 있었다. 그때 확정한 정의를 여기서 트랙 전체로 확장한다:

| 지표 | 정의 |
| --- | --- |
| `realized_pnl` | 금액. **대표값** — 가감 없는 사실 |
| `return_on_capital_pct` | 금액 ÷ 시작 자본 × 100. **자본 미상이면 `None`** |
| `unrealized_pnl` | **분리 표기.** 실현과 합산하지 않는다 |

## 현재 자본과 수익률은 **같은 기준**이다 (WO-FCE-REPORT-DEFECTS-01 7-1)

한동안 아니었다. 현재 자본은 NAV(현금 + 평가액)였고 수익률은 실현 ÷ 시작이었다. 그래서
같은 줄의 두 숫자가 다른 것을 말했다 — 실측 2026-08-29:

```
주식 KR   100,000,000 → 100,074,340 KRW (-0.00%)
          실현 -660.15 · 미실현 +75,000.00
```

**74,340 늘었는데 −0.00% 다.** 둘 다 각자 맞았지만 나란히 놓이면 거짓이었다.

```
현재 자본 = 시작 + 실현            ← 수익률과 같은 분자
NAV      = 현금 + 평가액           ← 별도 필드. 미실현을 포함한다
```

그래서 **`current_capital` 이 늘면 `return_on_capital_pct` 도 반드시 양수**이고, 그 부호
정합을 `test_capital_and_return_share_a_basis` 가 고정한다.

미실현을 자본에서 빼면 숫자가 나빠 보일 수 있다. **나빠 보이는 것이 맞다**(C2) —
미실현은 확정 손익이 아니고, 확정되지 않은 값을 자본에 섞은 것이 애초의 결함이다.

## 시작 자본을 역산하지 않는다

트랙마다 출처가 다르고, 없는 트랙이 있다.

| 트랙 | 출처 | 값 |
| --- | --- | --- |
| `crypto` | 설정 유도 — `paper_margin_usdt × paper_max_open_positions` | 500 USDT |
| `poly` | **원장** — `poly_paper_track.initial_cash` | 10,000 USDC |
| `stock_kr` · `stock_us` | **원장** — `stock_paper_tracks.initial_cash` | 1억 KRW · 10만 USD |
| `whale_follow` | **없음** | **미상** |

`whale_follow` 가 미상인 이유: 포지션 수 상한을 적용하지 않으므로 `margin × max_open` 으로
유도할 수 없다. 정책 객체에 `max_open_positions` 가 있지만 이 트랙은 **그것을 강제하지
않는다** — 강제하지 않는 상한으로 자본을 계산하면 실제 노출이 그 값을 넘을 수 있고, 그것은
자본이 아니라 추정이다. 미상은 미상으로 둔다(C7).

## 트랙 간 합산 금지

통화가 다르고(USDT·USDC·KRW·USD) 판정이 독립이다(`COMPLETION_DEFINITION`). 각 블록이
자기 통화를 들고 다니며, 이 모듈은 **합계를 만들지 않는다**(C2).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from app.validation import window_anchor

# 시작 자본의 출처. 무엇으로 정했는지가 값과 함께 다녀야 한다 — 근거 없는 자본은 쓰지 않는다.
SOURCE_SETTINGS = "settings_derived"
SOURCE_LEDGER = "ledger"
SOURCE_UNKNOWN = "unknown"
# 사용자·WO 가 **선언한** 값. 원장에도 설정 유도에도 없고 결정으로 정해진 것이다.
SOURCE_DECLARED = "declared_provisional"

# 이 미만이면 성적을 단정하지 않는다. `sample_viability.TARGET_SAMPLES` 와 같은 수다.
MIN_SAMPLE_FOR_VERDICT = 30

TRACK_CURRENCY = {
    "crypto": "USDT",
    "whale_follow": "USDT",
    "poly": "USDC",
    "stock_kr": "KRW",
    "stock_us": "USD",
}


@dataclass(frozen=True)
class Capital:
    """한 트랙의 자본. 미상이면 `amount` 가 `None` 이고 사유가 남는다."""

    amount: float | None
    source: str
    note: str

    @property
    def known(self) -> bool:
        return self.amount is not None and self.amount > 0


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return bool(connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _scalar(connection: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    try:
        row = connection.execute(sql, params).fetchone()
    except sqlite3.Error:
        return None
    return None if row is None else row[0]


def crypto_capital(settings: Any) -> Capital:
    """엔진 운용 자본. `METRIC_DEFINITIONS.md` §1 의 정의를 그대로 쓴다.

    하드코딩하지 않는다 — 설정이 바뀌면 자본도 바뀐다.
    """
    margin = float(getattr(settings, "paper_margin_usdt", 0.0) or 0.0)
    slots = int(getattr(settings, "paper_max_open_positions", 0) or 0)
    if margin <= 0 or slots <= 0:
        return Capital(None, SOURCE_UNKNOWN, "증거금 또는 동시 보유 상한이 설정에 없다")
    return Capital(margin * slots, SOURCE_SETTINGS, f"paper_margin_usdt {margin:g} × paper_max_open_positions {slots}")


def whale_follow_capital(settings: Any = None) -> Capital:
    """추종 트랙 시작 자본. **선언된 임시값**이다 (WO-FCE-DEFAULTS-01 1-1).

    처음에는 `미상` 이었다 — 동시 보유 상한을 강제하지 않아 `margin × max_open` 으로
    유도하면 실제 노출을 밑돌 수 있었기 때문이다. 그 지적은 옳았고, 그래서 이번에는
    **자본과 상한을 함께 선언한다.** 상한 없이 자본만 넣으면 그 자본이 거짓이 된다.

    값은 크립토 트랙과 같다(500 USDT = 100 × 5). 두 트랙을 같은 자본에서 비교할 수 있어야
    한다. 유리하게 잡지 않았다 — 크립토와 동일하고, 실현이 음수면 음수로 나온다(C8).

    원복: `FCE_WHALE_FOLLOW_STARTING_CAPITAL_USDT=0` 이면 다시 `미상` 이 된다.
    """
    declared = float(getattr(settings, "whale_follow_starting_capital_usdt", 0.0) or 0.0)
    slots = int(getattr(settings, "whale_follow_max_open_positions", 0) or 0)
    if declared <= 0:
        return Capital(
            None,
            SOURCE_UNKNOWN,
            "시작 자본이 선언되지 않았다 — 동시 보유 상한을 강제하지 않으면 margin × max_open 으로 유도할 수 없다.",
        )
    note = f"임시값 · 크립토 트랙과 동일(500 = margin 100 × 5) · 동시 보유 상한 {slots}건 강제"
    if slots <= 0:
        # 상한이 풀린 상태의 자본은 실제 노출을 밑돌 수 있다. 그 사실을 숨기지 않는다.
        note += " — **상한이 0 이라 강제되지 않는다. 실제 노출이 자본을 넘을 수 있다.**"
    return Capital(declared, SOURCE_DECLARED, note)


def ledger_capital(connection: sqlite3.Connection, *, table: str, column: str = "initial_cash", where: str = "", params: tuple[Any, ...] = ()) -> Capital:
    """원장에 기록된 시작 자본. 기록이 없으면 미상이다."""
    if not _table_exists(connection, table):
        return Capital(None, SOURCE_UNKNOWN, f"{table} 테이블이 없다")
    clause = f" WHERE {where}" if where else ""
    value = _scalar(connection, f"SELECT {column} FROM {table}{clause} LIMIT 1", params)
    if value is None:
        return Capital(None, SOURCE_UNKNOWN, f"{table}.{column} 행이 없다")
    amount = float(value)
    if amount <= 0:
        return Capital(None, SOURCE_UNKNOWN, f"{table}.{column} 가 0 이하다")
    return Capital(amount, SOURCE_LEDGER, f"{table}.{column}")


def _paper_track_pnl(connection: sqlite3.Connection, table: str, *, anchor: window_anchor.WindowAnchor | None) -> dict[str, Any]:
    """`paper_trades` 계열 원장의 실현·미실현. 창 술어는 `entry_bar_at >= 앵커`(C3)."""
    if not _table_exists(connection, table):
        return {"available": False, "reason": f"{table} 테이블이 없다"}
    query, params = window_anchor.since_clause(anchor, inner_sql=f"SELECT entry_bar_at AS t, status, payload FROM {table}")
    try:
        rows = connection.execute(query, params).fetchall()
    except sqlite3.Error as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
    realized = 0.0
    unrealized = 0.0
    closed = 0
    open_count = 0
    for row in rows:
        try:
            payload = json.loads(row["payload"] if isinstance(row, sqlite3.Row) else row[2])
        except (TypeError, ValueError):
            continue
        net = float(payload.get("net_pnl_usdt") or 0.0)
        if str(payload.get("status") or "") == "closed":
            realized += net
            closed += 1
        else:
            # C4 — 미실현은 실현에 더하지 않는다. 확정되지 않은 값이다.
            unrealized += net
            open_count += 1
    return {"available": True, "realized": round(realized, 4), "unrealized": round(unrealized, 4), "closed": closed, "open": open_count}


def _cash_track_pnl(
    connection: sqlite3.Connection,
    *,
    table: str,
    where: str = "",
    params: tuple[Any, ...] = (),
    cost_basis: float | None = None,
    mark_value: float | None = None,
) -> dict[str, Any]:
    """현금 잔액을 들고 있는 원장(폴리·주식)의 실현·미실현.

    ## `cash − initial_cash` 는 실현 손익이 아니다

    처음 구현이 그것을 실현으로 썼고 **틀렸다.** 그 차액에는 열린 포지션에 **묶인 현금**이
    들어 있다. 실측 2026-08-26 이 그것을 그대로 보여준다:

    | 트랙 | 현금 부족분 | 포지션 원가 | 실제 실현 |
    | --- | --- | --- | --- |
    | `stock_kr` | 4,401,660.15 | 4,401,000.00 | **−660.15** (수수료) |
    | `stock_us` | 2,008.32 | 2,012.14 | **+3.82** |
    | `poly` | 1,583.1227 | 1,583.1227 | **0** (정확히) |

    폴리는 두 값이 소수점까지 같다 — 아무것도 실현되지 않았는데 `−15.83%` 로 찍혔다.
    그것이 C4 가 막으려는 혼동이고 이 WO 가 고치려는 결함 그 자체였다.

    그래서:

        realized      = (cash − initial_cash) + 포지션 원가
        unrealized    = 평가액 − 포지션 원가        (평가액 없으면 미상)
        current(NAV)  = cash + 평가액               (평가액 없으면 미상)
    """
    if not _table_exists(connection, table):
        return {"available": False, "reason": f"{table} 테이블이 없다"}
    clause = f" WHERE {where}" if where else ""
    row = connection.execute(f"SELECT initial_cash, cash FROM {table}{clause} LIMIT 1", params).fetchone()
    if row is None:
        return {"available": False, "reason": f"{table} 행이 없다"}
    initial = float(row[0] or 0.0)
    cash = float(row[1] or 0.0)
    cost = float(cost_basis or 0.0)
    return {
        "available": True,
        "realized": round((cash - initial) + cost, 4),
        "cash": round(cash, 4),
        "deployed_capital": round(cost, 4),
        "unrealized": round(float(mark_value) - cost, 4) if mark_value is not None else None,
        "nav": round(cash + float(mark_value), 4) if mark_value is not None else None,
        "unrealized_note": (
            "실현과 합산하지 않는다 — 확정되지 않은 값이다(C4)"
            if mark_value is not None
            else "보유 포지션 평가액을 읽을 수 없다 — 미실현 미상. 역산하지 않는다(C7)"
        ),
    }


def _stock_position_values(connection: sqlite3.Connection, market: str) -> tuple[float, float | None]:
    """주식 포지션의 원가와 평가액. 마크가 하나라도 없으면 평가액은 `None` 이다."""
    if not _table_exists(connection, "stock_paper_positions"):
        return 0.0, None
    rows = connection.execute(
        """SELECT p.quantity, p.average_price, m.price
        FROM stock_paper_positions p LEFT JOIN stock_paper_marks m
          ON m.market = p.market AND m.symbol = p.symbol
        WHERE p.market = ?""",
        (market,),
    ).fetchall()
    cost = sum(int(row[0]) * float(row[1]) for row in rows)
    if any(row[2] is None for row in rows):
        # 일부 마크가 없으면 평가액을 만들지 않는다 — 부분 평가액은 NAV 를 왜곡한다.
        return round(cost, 4), None
    return round(cost, 4), round(sum(int(row[0]) * float(row[2]) for row in rows), 4)


def _poly_position_values(connection: sqlite3.Connection) -> tuple[float, float | None]:
    """폴리 포지션의 원가와 평가액.

    평가액은 현재 시장가가 있어야 만들 수 있다. 수집이 막혀 있으면(실측 451 지역 제한)
    가격이 없으므로 **미상**이다 — 원가로 대신하면 미실현이 항상 0 으로 보인다.
    """
    if not _table_exists(connection, "poly_positions"):
        return 0.0, None
    cost = float(_scalar(connection, "SELECT COALESCE(SUM(cost), 0) FROM poly_positions") or 0.0)
    return round(cost, 4), None


def track_capital(connection: sqlite3.Connection, settings: Any, track: str) -> dict[str, Any]:
    """한 트랙의 자본 블록. 통화가 다르므로 합산하지 않는다(C2)."""
    currency = TRACK_CURRENCY.get(track, "?")
    anchor = window_anchor.current_anchor(connection, track)

    if track == "crypto":
        capital = crypto_capital(settings)
        flows = _paper_track_pnl(connection, "paper_trades", anchor=anchor)
    elif track == "whale_follow":
        capital = whale_follow_capital(settings)
        flows = _paper_track_pnl(connection, "whale_follow_trades", anchor=anchor)
    elif track == "poly":
        capital = ledger_capital(connection, table="poly_paper_track")
        cost, marks = _poly_position_values(connection)
        flows = _cash_track_pnl(connection, table="poly_paper_track", cost_basis=cost, mark_value=marks)
    elif track in {"stock_kr", "stock_us"}:
        market = "KR" if track == "stock_kr" else "US"
        capital = ledger_capital(connection, table="stock_paper_tracks", where="market=?", params=(market,))
        cost, marks = _stock_position_values(connection, market)
        flows = _cash_track_pnl(connection, table="stock_paper_tracks", where="market=?", params=(market,), cost_basis=cost, mark_value=marks)
    else:
        return {"track": track, "available": False, "reason": f"정의되지 않은 트랙: {track}"}

    realized = flows.get("realized") if flows.get("available") else None
    unrealized = flows.get("unrealized") if flows.get("available") else None
    deployed = flows.get("deployed_capital")

    # ── 현재 자본 = 시작 + **실현**. 수익률과 같은 분자다(7-1) ──────────
    #
    # 이 값에 미실현을 넣으면 아래 `return_pct` 와 기준이 갈리고, 갈린 두 수가 같은 줄에
    # 놓이면 "74,340 늘었는데 −0.00%" 가 된다. 평가액을 포함한 값은 `nav` 로 따로 낸다.
    current = round(float(capital.amount or 0.0) + float(realized), 4) if capital.known and realized is not None else None

    # NAV — **다른 질문의 답이다.** "지금 다 정리하면 얼마인가".
    # 현금 트랙은 원장이 직접 준다. 페이퍼 트랙은 시작 + 실현 + 미실현이며, 미실현을
    # 모르면 만들지 않는다(C7 — 원가로 대신하면 미실현이 항상 0 으로 보인다).
    nav = flows.get("nav")
    if nav is None and current is not None and unrealized is not None:
        nav = round(current + float(unrealized), 4)

    # 평가 불가 포지션이 있으면 NAV 를 부르지 않는다. 실측에서 폴리가 그랬다 — 1,583 USDC
    # 가 값을 모르는 포지션에 묶여 있는데 "원금 그대로"로 보이면 그것이 거짓이다.
    unpriced = bool(float(deployed or 0.0)) and unrealized is None

    # C1 — 자본이 미상이면 수익률을 만들지 않는다. 대표값은 금액이다.
    return_pct = round(float(realized) / float(capital.amount) * 100, 4) if capital.known and realized is not None else None
    closed = int(flows.get("closed") or 0) if flows.get("available") else 0
    return {
        "track": track,
        "available": bool(flows.get("available")),
        "reason": flows.get("reason"),
        "currency": currency,
        "starting_capital": capital.amount,
        "starting_capital_source": capital.source,
        "starting_capital_note": capital.note,
        "current_capital": current,
        # 7-1 — 현재 자본이 무엇을 세는지 값과 함께 다닌다. 라벨 없는 자본이 D1 이었다.
        "current_capital_basis": "realized",
        "current_capital_basis_note": "시작 자본 + 실현 손익. 미실현은 포함하지 않는다 — 수익률과 같은 기준이다(C3).",
        "nav": nav,
        "nav_note": (
            "현금 + 보유 평가액 — **미실현을 포함한다.** 현재 자본과 다른 질문의 답이며 수익률의 분자가 아니다."
            if nav is not None
            else "보유 포지션 평가액을 읽을 수 없어 NAV 를 만들지 않는다(C7)"
        ),
        "unpriced_positions": unpriced,
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "unrealized_note": flows.get("unrealized_note") or "실현과 합산하지 않는다 — 확정되지 않은 값이다(C4)",
        "return_on_capital_pct": return_pct,
        "return_note": None if capital.known else "시작 자본 미상 — 수익률 미산출(C7)",
        "deployed_capital": flows.get("deployed_capital"),
        "current_capital_note": (
            "시작 자본 미상 — 현재 자본 미산출(C7)"
            if current is None
            else (f"{deployed:,.4f} {currency} 가 평가 불가 포지션에 묶여 있다 — NAV 미상" if unpriced else None)
        ),
        "closed_samples": closed,
        "open_positions": int(flows.get("open") or 0) if flows.get("available") else None,
        "sample_sufficient": closed >= MIN_SAMPLE_FOR_VERDICT,
        "sample_note": f"청산 {closed}건 — N<{MIN_SAMPLE_FOR_VERDICT} 이므로 성적으로 단정하지 않는다"
        if closed < MIN_SAMPLE_FOR_VERDICT
        else f"청산 {closed}건",
        "window_anchor": anchor.anchored_at.isoformat() if anchor else None,
        "window_predicate": "entry >= 앵커 (앵커 없으면 전체)",
    }


def all_tracks(connection: sqlite3.Connection, settings: Any) -> dict[str, Any]:
    """4트랙 자본 블록. **합계를 만들지 않는다** — 통화가 다르고 판정이 독립이다(C2)."""
    return {
        "tracks": {track: track_capital(connection, settings, track) for track in TRACK_CURRENCY},
        "no_total": "트랙 간 자본을 합산하지 않는다 — 통화가 다르고 트랙별 독립 판정이 규정이다(C2)",
        "definition": "docs/validation/METRIC_DEFINITIONS.md §1 — 금액이 대표값이고 자본 대비 수익률을 병기한다",
    }


def capital_for_response(settings: Any, tracks: tuple[str, ...]) -> dict[str, Any]:
    """응답에 실을 자본 블록. 라우트 계층에서 쓴다 — 스토어·원장을 건드리지 않는다(C6).

    조회 실패가 대시보드를 죽이면 안 되므로 사유를 담아 돌려준다. 실패를 0 으로 채우지
    않는다 — 0 은 값이고 미상은 미상이다(C7).
    """
    database_url = str(getattr(settings, "database_url", "") or "")
    path = database_url.removeprefix("sqlite:///") if database_url.startswith("sqlite:///") else ""
    if not path:
        return {"available": False, "reason": "SQLite 경로가 아니다 — 자본 원장을 읽을 수 없다", "tracks": {}}
    try:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        try:
            return {"available": True, "tracks": {track: track_capital(connection, settings, track) for track in tracks}, "definition": DEFINITION_DOC}
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}", "tracks": {}}


DEFINITION_DOC = "docs/validation/METRIC_DEFINITIONS.md §1 — 금액이 대표값이고 자본 대비 수익률을 병기한다. 미실현은 합산하지 않는다."
