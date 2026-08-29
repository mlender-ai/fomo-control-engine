"""WO-FCE-WHALE-EXIT-REPLAY-01 — 출구 A/B 반사실 대조.

## 왜 필요한가

추종 트랙이 반쪽이다. **진입만 고래를 따라가고 청산은 우리 규칙이다** —
`whale_follow.run_exits` 는 `paper_policy.evaluate_exit` 를 부르고 고래가 청산했는지
보지 않는다.

그리고 비대칭이 하나 더 있다:

| | 트리거 | 주기 |
| --- | --- | --- |
| 진입 | 고래 체결(이벤트) | 0.2~20분 |
| 청산 | 확정봉 + 우리 사다리 | **4시간봉** |

실측 2026-08-29 의 청산 시각이 그것을 그대로 보여준다 — 대부분 `00:00`·`08:00`·`12:00`·
`20:00` 정각이다. 고래는 분 단위로 움직이는데 우리는 봉 경계에서만 나온다.

> **−14.96 USDT 의 원인이 고래 신호인지 우리 출구인지 지금 데이터로는 안 갈린다.**

## 반사실이다 — 포지션을 두 배로 열지 않는다 (C1)

출구 B 는 **계산만 한다.** 같은 진입에 실제 포지션을 둘 열면 리스크가 두 배가 되고 표본도
이중 계상된다. 트랙의 공식 표본은 **출구 A 하나**이며 B 는 병기 필드다(C2).

## 수집은 이미 되고 있었다

WO 2-1 은 "감액·청산 체결을 수집·저장한다"였는데 **이미 저장되고 있다** —
`whale_events` 에 `reduce` 19,916건 · `close` 902건. 쓰지 않았을 뿐이다.
37건 전부 고래 청산이 매칭된다. 그래서 이 모듈은 수집기가 아니라 **대조기**다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

# 청산으로 보는 고래 체결. 증액(`open`·`increase`·`flip`)은 진입 신호이고 여기 없다.
EXIT_EVENTS = ("reduce", "close")

# 누가 먼저 나왔는가. 차이의 원인을 이 축으로 분해한다(2-2 항목 3).
LEAD_OURS = "ours_first"
LEAD_WHALE = "whale_first"
LEAD_NONE = "whale_open"

# 이 미만이면 대조 결과를 성적으로 단정하지 않는다(C11).
MIN_SAMPLE = 30


@dataclass(frozen=True)
class WhaleExit:
    """매칭된 고래 청산 한 건. 부분과 전량을 구분한다(2-1 항목 3)."""

    at: datetime
    price: float | None
    kind: str
    size_usd: float

    @property
    def full_exit(self) -> bool:
        return self.kind == "close"


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def counterfactual_pnl(
    *,
    direction: str,
    entry_price: float,
    quantity: float,
    exit_price: float,
    cost_rate: float,
    entry_cost: float,
) -> float:
    """출구 B 로 청산했을 때의 **순손익**.

    출구 A 와 같은 비용 모델을 쓴다 — 비용이 다르면 두 값의 차이가 출구 차이인지 비용
    차이인지 갈리지 않는다. `policy.apply_exit_decision` 과 같은 식이다.
    """
    gross = (exit_price - entry_price) * quantity if direction == "long" else (entry_price - exit_price) * quantity
    exit_cost = exit_price * quantity * cost_rate
    return round(gross - entry_cost - exit_cost, 6)


def match_whale_exit(rows: list[Any], *, after: datetime) -> WhaleExit | None:
    """진입 이후 **가장 이른** 고래 청산. 없으면 `None`(고래가 아직 보유 중)."""
    for row in sorted(rows, key=lambda item: str(item["event_at"])):
        at = _parse(row["event_at"])
        if at is None or at <= after:
            continue
        kind = str(row["event_type"] or "")
        if kind not in EXIT_EVENTS:
            continue
        return WhaleExit(at=at, price=_float(row["price"]), kind=kind, size_usd=float(row["size_usd"] or 0.0))
    return None


def _parse(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def compare_trade(trade: dict[str, Any], whale_exit: WhaleExit | None, *, cost_rate: float) -> dict[str, Any]:
    """한 거래의 A/B 대조. B 를 만들 수 없으면 **만들지 않는다**(추정치를 적지 않는다)."""
    actual_net = _float(trade.get("net_pnl_usdt"))
    our_exit_at = _parse(trade.get("exit_at"))
    row: dict[str, Any] = {
        "id": trade.get("id"),
        "symbol": trade.get("symbol"),
        "direction": trade.get("direction"),
        "whale_address": trade.get("whale_address"),
        "exit_a_net": actual_net,
        "exit_a_at": our_exit_at.isoformat() if our_exit_at else None,
        "exit_a_reason": trade.get("exit_reason"),
        "exit_b_net": None,
        "exit_b_at": None,
        "exit_b_kind": None,
        "delta": None,
        "lead": LEAD_NONE,
        "note": None,
    }
    if whale_exit is None:
        row["note"] = "고래가 아직 청산하지 않았다 — 반사실을 만들지 않는다"
        return row
    row["exit_b_at"] = whale_exit.at.isoformat()
    row["exit_b_kind"] = whale_exit.kind
    row["lead"] = LEAD_OURS if (our_exit_at and our_exit_at <= whale_exit.at) else LEAD_WHALE
    entry_price = _float(trade.get("entry_price"))
    quantity = _float(trade.get("quantity"))
    if whale_exit.price is None or entry_price is None or quantity is None:
        row["note"] = "고래 체결가를 읽을 수 없다 — 반사실 손익 미산출"
        return row
    entry_cost = entry_price * quantity * cost_rate
    row["exit_b_net"] = counterfactual_pnl(
        direction=str(trade.get("direction") or "long"),
        entry_price=entry_price,
        quantity=quantity,
        exit_price=whale_exit.price,
        cost_rate=cost_rate,
        entry_cost=entry_cost,
    )
    if actual_net is not None:
        row["delta"] = round(row["exit_b_net"] - actual_net, 6)
    return row


def _bucket() -> dict[str, Any]:
    return {"count": 0, "a_net": 0.0, "b_net": 0.0, "a_wins": 0, "b_wins": 0, "a_profit": 0.0, "a_loss": 0.0, "b_profit": 0.0, "b_loss": 0.0}


def _finish(bucket: dict[str, Any]) -> dict[str, Any]:
    count = int(bucket["count"])
    for side in ("a", "b"):
        loss = float(bucket.pop(f"{side}_loss"))
        profit = float(bucket.pop(f"{side}_profit"))
        bucket[f"{side}_net"] = round(float(bucket[f"{side}_net"]), 4)
        bucket[f"{side}_win_pct"] = round(int(bucket[f"{side}_wins"]) / count * 100, 1) if count else None
        # 손실 0건이면 PF 를 만들지 않는다 — 표본이 작을 때 무한대는 거짓 확신이다.
        bucket[f"{side}_profit_factor"] = round(profit / loss, 2) if loss > 0 else None
    bucket["delta_net"] = round(float(bucket["b_net"]) - float(bucket["a_net"]), 4)
    bucket["sample_sufficient"] = count >= MIN_SAMPLE
    bucket["sample_note"] = f"N={count}" + ("" if count >= MIN_SAMPLE else f" — N<{MIN_SAMPLE} 이므로 성적으로 단정하지 않는다")
    return bucket


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """전체·지갑별 A vs B 대조와 차이 원인 분해."""
    overall = _bucket()
    by_wallet: dict[str, dict[str, Any]] = {}
    leads: dict[str, int] = {LEAD_OURS: 0, LEAD_WHALE: 0, LEAD_NONE: 0}
    comparable = 0
    for row in rows:
        leads[str(row.get("lead") or LEAD_NONE)] = leads.get(str(row.get("lead") or LEAD_NONE), 0) + 1
        a_net = row.get("exit_a_net")
        b_net = row.get("exit_b_net")
        if a_net is None or b_net is None:
            continue
        comparable += 1
        wallet = str(row.get("whale_address") or "unknown")
        for bucket in (overall, by_wallet.setdefault(wallet, _bucket())):
            bucket["count"] += 1
            bucket["a_net"] += float(a_net)
            bucket["b_net"] += float(b_net)
            for side, value in (("a", float(a_net)), ("b", float(b_net))):
                if value > 0:
                    bucket[f"{side}_wins"] += 1
                    bucket[f"{side}_profit"] += value
                else:
                    bucket[f"{side}_loss"] += abs(value)
    return {
        "overall": _finish(overall),
        "by_wallet": {wallet: _finish(bucket) for wallet, bucket in by_wallet.items()},
        "lead_breakdown": leads,
        "comparable": comparable,
        "total": len(rows),
        # C2 — 공식 표본은 A 하나다. 이 블록은 병기이며 트랙 판정에 합산하지 않는다.
        "official_sample": "exit_a",
        "not_official": "출구 B 는 반사실이며 whale_follow 트랙 표본에 합산하지 않는다(C2). 실적으로 보고하지 않는다(C11).",
    }


def verdict(summary: dict[str, Any], *, inflation: dict[str, Any] | None = None) -> dict[str, Any]:
    """**−14.96 이 신호 탓인지 출구 탓인지** 판정한다.

    판정을 세 갈래로만 낸다. 표본이 부족하면 그 사실을 판정 자리에 쓴다 — 부족한 표본으로
    방향을 정하면 다음 WO 전체가 그 위에 얹힌다.
    """
    overall = summary["overall"]
    count = int(overall["count"])
    delta = overall.get("delta_net")
    if count == 0:
        return {"verdict": "UNDETERMINED", "reason": "대조 가능한 거래가 없다", "actionable": False}
    if count < MIN_SAMPLE:
        return {
            "verdict": "INSUFFICIENT_SAMPLE",
            "reason": f"대조 {count}건 — N<{MIN_SAMPLE}. 방향은 관측되나 판정으로 쓰지 않는다",
            "observed_delta_usdt": delta,
            "actionable": False,
        }
    # 자본 대비로 재지 않는다 — 두 출구가 같은 진입·같은 사이즈이므로 금액 차이가 곧 출구 차이다.
    if delta is not None and delta > 0:
        result: dict[str, Any] = {
            "verdict": "EXIT_B_BETTER",
            "reason": "고래 청산을 따라갔으면 더 나았다 — 우리 출구가 신호와 맞지 않는다",
            "observed_delta_usdt": delta,
            "actionable": True,
        }
        if inflation and inflation.get("detected"):
            # 이 경고가 없으면 "출구를 고래 추종으로 전환" 이라는 결론이 버그를 설계로 굳힌다.
            result["caveat"] = (
                f"출구 A 에 결함이 있다 — holding_bars 가 봉이 아니라 잡 실행 횟수를 센다({inflation['count']}건 확인). "
                "이 비교는 '우리 사다리 대 고래 청산'이 아니라 '버그 있는 사다리 대 고래 청산'이다. "
                "출구를 전환하기 전에 그 결함부터 고치고 다시 대조해야 한다."
            )
            result["actionable"] = False
        return result
    if delta is not None and delta < 0:
        return {"verdict": "EXIT_A_BETTER", "reason": "우리 출구가 낫다 — 손실 원인은 진입 신호 쪽이다", "observed_delta_usdt": delta, "actionable": True}
    return {"verdict": "NO_DIFFERENCE", "reason": "출구는 원인이 아니다 — 신호 쪽을 본다", "observed_delta_usdt": delta, "actionable": True}


class EventCache:
    """(지갑, 심볼)당 **한 번만** 조회한다.

    거래마다 조회하면 청산 37건에 37질의가 나가고, 워커가 같은 SQLite 에 쓰는 동안 그 질의
    하나하나가 락을 기다린다. 측정으로 확인했다 — 고래 탭 응답이 9~14초에서 14~29초로
    늘었다. 진단 화면이 느려서 안 보게 되면 진단이 없는 것과 같다.

    전역 최근 N건으로 대신하지 않는다. 최근 2000건은 이틀치이고 추종 거래는 그보다 오래
    거슬러 올라간다 — 잘린 창으로 대조하면 "고래가 청산하지 않았다"가 거짓으로 나온다.
    """

    def __init__(self, repo: Any, *, limit: int = 500) -> None:
        self._repo = repo
        self._limit = limit
        self._cache: dict[tuple[str, str], list[Any]] = {}

    def events(self, wallet: str, symbol: str) -> list[Any]:
        key = (wallet.lower(), symbol.upper())
        if key not in self._cache:
            self._cache[key] = list(self._repo.list_whale_events(wallet_address=wallet, symbol=symbol, limit=self._limit))
        return self._cache[key]


def build_comparison(repo: Any, settings: Any, *, limit: int = 500, cache: EventCache | None = None) -> dict[str, Any]:
    """원장에서 A/B 대조를 만든다. 네트워크를 타지 않는다(C9) — 저장된 체결만 읽는다."""
    from app.paper import service as paper_service

    # 비용률을 직접 만들지 않는다 — 출구 A 와 **같은 정책 객체**에서 읽는다. 비용이 다르면
    # A/B 차이가 출구 차이인지 비용 차이인지 갈리지 않는다.
    cost_rate = float(paper_service.policy_from_settings(settings, "crypto").execution_cost_rate)
    cache = cache or EventCache(repo)

    rows: list[dict[str, Any]] = []
    for trade in repo.list_whale_follow_trades(limit=limit):
        evidence = trade.entry_evidence or {}
        wallet = str(evidence.get("whale_address") or "").lower()
        if not wallet:
            continue
        anchor = _parse(evidence.get("whale_event_at")) or trade.entry_at
        events = cache.events(wallet, trade.symbol)
        candidates = [
            {"event_at": item.event_at, "event_type": item.event, "price": item.entry_px, "size_usd": item.size_usd}
            for item in events
            if item.event in EXIT_EVENTS
        ]
        whale_exit = match_whale_exit(candidates, after=anchor) if anchor else None
        rows.append(
            compare_trade(
                {
                    "id": str(trade.id),
                    "symbol": trade.symbol,
                    "direction": trade.direction.value,
                    "whale_address": wallet,
                    "net_pnl_usdt": trade.net_pnl_usdt,
                    "exit_at": trade.exit_at,
                    "exit_reason": trade.exit_reason,
                    "entry_price": trade.entry_price,
                    "quantity": trade.quantity,
                },
                whale_exit,
                cost_rate=cost_rate,
            )
        )
    summary = summarize(rows)
    inflation = detect_holding_bar_inflation(repo.list_whale_follow_trades(limit=limit))
    return {
        "trades": rows,
        **summary,
        "verdict": verdict(summary, inflation=inflation),
        "holding_bar_inflation": inflation,
        "cost_rate": cost_rate,
    }


def detect_holding_bar_inflation(trades: list[Any], *, timeframe_hours: float = 4.0) -> dict[str, Any]:
    """`holding_bars` 가 봉이 아니라 **잡 실행 횟수**를 세는지 검사한다.

    ## 왜 이 검사가 A/B 판정에 필요한가

    실측 2026-08-29: `time_decay` 로 닫힌 21건이 전부 `holding_bars=30`(상한)인데
    **실경과 시간이 0.18~4.0시간**이다. 30봉이면 120시간이어야 한다. 한 건은 진입 직후
    0.0시간에 시간 만료로 닫혔다.

    원인은 `run_exits` 의 가드가 **진입봉**만 비교하고 마지막 평가봉을 기억하지 않는 것이다:

    ```python
    if bar is None or bar.timestamp <= trade.entry_bar_at:   # 진입봉 이후면 항상 통과
    ```

    추종 잡은 900초 주기 + 체결 구동이므로 같은 봉을 여러 번 평가하고, 그때마다
    `apply_exit_decision` 이 `holding_bars += 1` 을 한다. 상한 30 이 설계 의도(5일)의
    **1/30 시점**에 채워진다.

    ## 그래서 A/B 비교의 의미가 달라진다

    이 결함이 있으면 A/B 는 "우리 사다리 대 고래 청산"이 아니라
    **"버그 있는 사다리 대 고래 청산"** 이다. 그 구분 없이 "출구를 고래 추종으로 전환"
    이라는 결론을 내면 **버그를 설계로 굳히게 된다.**

    이 WO 는 출구 A 를 고치지 않는다(C4) — 비교 대상이 바뀌면 비교가 무의미하기 때문이다.
    대신 그 사실을 판정에 실어 보낸다.
    """
    inflated: list[dict[str, Any]] = []
    for trade in trades:
        bars = int(getattr(trade, "holding_bars", 0) or 0)
        entry_at = getattr(trade, "entry_at", None)
        exit_at = getattr(trade, "exit_at", None)
        if bars <= 0 or entry_at is None or exit_at is None:
            continue
        elapsed_hours = (exit_at - entry_at).total_seconds() / 3600.0
        implied_hours = bars * float(timeframe_hours)
        # 실경과가 함의된 시간의 절반도 안 되면 봉이 아니라 실행 횟수를 센 것이다.
        if implied_hours > 0 and elapsed_hours < implied_hours * 0.5:
            inflated.append(
                {
                    "id": str(getattr(trade, "id", "")),
                    "symbol": getattr(trade, "symbol", None),
                    "holding_bars": bars,
                    "elapsed_hours": round(elapsed_hours, 2),
                    "implied_hours": round(implied_hours, 2),
                    "exit_reason": getattr(trade, "exit_reason", None),
                }
            )
    return {
        "detected": bool(inflated),
        "count": len(inflated),
        "sample": inflated[:5],
        "mechanism": (
            "run_exits 의 가드가 진입봉만 비교하고 마지막 평가봉을 기억하지 않는다 — 900초 주기 잡이 같은 봉을 여러 번 평가하며 매번 holding_bars 를 올린다."
        ),
        "impact": "시간 만료 상한이 설계 의도의 1/30 시점에 발화한다",
        "not_fixed_here": "출구 A 를 고치면 비교 대상이 바뀌어 A/B 가 무의미해진다(C4). 별건이다.",
    }
