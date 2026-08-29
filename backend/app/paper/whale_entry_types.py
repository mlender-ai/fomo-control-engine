"""WO-FCE-WHALE-EXIT-REPLAY-01 2-3 — 고래 진입·청산 복기.

## 왜

**"누가 샀다"만 알고 "왜 샀는지"는 모른 채 따라가고 있다.** 지금 자격은
`N>=30 · 승률>=55% · MM 아님` 뿐이고 **어떤 종류의 진입이냐는 보지 않는다.**

## 있는 것으로만 만든다

크립토 캔들이 저장돼 있지 않다(`market_candles` 테이블 없음). 그래서 WO 가 나열한 맥락 중
일부는 네트워크 없이 소급 산출할 수 없고, C9 가 임계 경로의 네트워크를 금지한다.

| 맥락 | 산출 |
| --- | --- |
| 다른 추적 지갑의 동시 방향 (무리/단독) | **가능** — `whale_events` |
| 시각대 | **가능** |
| 직전 가격 움직임 | **대리 지표** — 같은 심볼의 직전 고래 체결가 대비 |
| 보유 시간 · 고래 손익 방향 | **가능** — `closed_pnl` |
| 이익 실현인가 손절인가 | **가능** |
| 부분/전량 | **가능** |
| 레인지 내 위치 · 거래량 · 펀딩 · OI | **미산출** — 저장된 캔들이 없다 |

**미산출을 추정으로 채우지 않는다.** 없는 맥락으로 유형을 만들면 그 유형이 거짓이 된다.

## 추정이다 (C6)

모든 반환에 `estimate=True` 가 붙고 신뢰도가 병기된다. 근거가 부족하면 `미분류`이며
그것이 다수여도 그대로 낸다 — 분류율을 올리려고 문턱을 낮추지 않는다.

## 선정 기준에 넣지 않는다 (C8)

유형별 성적은 **사후 채점**이다. `OBSERVATION-INTEGRITY-01` Phase 5 가 승률을 선정에
넣지 않기로 한 것과 같은 경계다 — 사후 지표를 선정에 넣으면 그 지표가 자기실현된다.

## 인과를 단정하지 않는다 (C7)

"고래가 팔아서 떨어졌다"가 아니라 "고래 청산과 하락이 같은 창에서 관측됐다"이다.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

TYPE_HERD = "herd"
TYPE_SOLO = "solo"
TYPE_UNCLASSIFIED = "unclassified"

# 무리 판정 창. 이 안에 같은 심볼·같은 방향으로 다른 추적 지갑이 들어왔는가.
HERD_WINDOW_MINUTES = 30
# 무리로 부르는 최소 동반 지갑 수. 1이면 우연이 무리가 된다.
HERD_MIN_PEERS = 2

EXIT_TAKE_PROFIT = "take_profit"
EXIT_STOP = "stop"
EXIT_FLAT = "flat"

# 산출할 수 없는 맥락. 이름을 박아 둔다 — 나중에 캔들이 생기면 여기가 착수 지점이다.
UNAVAILABLE_CONTEXT = ("range_position", "volume", "funding", "open_interest")


def _hour_bucket(at: datetime) -> str:
    """UTC 시각대. 세션 구분의 대리 지표이며 거래소 세션과 정확히 대응하지 않는다."""
    hour = at.hour
    if 0 <= hour < 8:
        return "asia"
    if 8 <= hour < 16:
        return "europe"
    return "us"


def herd_context(
    *,
    address: str,
    symbol: str,
    direction: str,
    at: datetime,
    peer_events: list[Any],
    window_minutes: int = HERD_WINDOW_MINUTES,
) -> dict[str, Any]:
    """같은 창에 같은 방향으로 들어온 **다른 추적 지갑** 수.

    혼자 들어간 진입과 여럿이 같이 들어간 진입은 다른 사건이다. 그런데 지금 자격은 그
    구분을 하지 않는다.
    """
    start = at - timedelta(minutes=max(1, int(window_minutes)))
    peers: set[str] = set()
    for event in peer_events:
        other = str(getattr(event, "wallet_address", "") or "").lower()
        if not other or other == address.lower():
            continue
        if str(getattr(event, "symbol", "") or "").upper() != symbol.upper():
            continue
        if str(getattr(event, "side", "") or "") != direction:
            continue
        if str(getattr(event, "event", "") or "") not in {"open", "increase", "flip"}:
            continue
        stamp = getattr(event, "event_at", None)
        if stamp is None or not (start <= stamp <= at):
            continue
        peers.add(other)
    return {"peer_wallets": len(peers), "window_minutes": int(window_minutes), "peers": sorted(peers)[:5]}


def classify_entry(*, herd: dict[str, Any], at: datetime, price_move_pct: float | None) -> dict[str, Any]:
    """진입 유형 추정. 근거가 부족하면 **미분류**다(C6).

    지금은 무리/단독만 가른다. 돌파 추종·되돌림·청산 캐스케이드는 **레인지와 거래량이
    있어야** 구분되는데 그 데이터가 없다 — 없는 것으로 유형을 만들면 그 유형이 거짓이다.
    """
    peers = int(herd.get("peer_wallets") or 0)
    session = _hour_bucket(at)
    base = {
        "estimate": True,
        "session": session,
        "peer_wallets": peers,
        "price_move_pct": price_move_pct,
        "unavailable_context": list(UNAVAILABLE_CONTEXT),
        "basis": "다른 추적 지갑의 동시 방향·시각대 — 저장된 체결만 사용(C9). 신원·의도 확정이 아님",
    }
    if peers >= HERD_MIN_PEERS:
        return {
            **base,
            "entry_type": TYPE_HERD,
            "confidence": round(min(0.9, 0.4 + peers * 0.15), 2),
            "reason": f"{herd['window_minutes']}분 창에 다른 추적 지갑 {peers}개가 같은 방향으로 들어왔다",
        }
    if peers == 0:
        return {
            **base,
            "entry_type": TYPE_SOLO,
            "confidence": 0.5,
            "reason": f"{herd['window_minutes']}분 창에 같은 방향 동반 지갑이 없다",
        }
    return {
        **base,
        "entry_type": TYPE_UNCLASSIFIED,
        "confidence": 0.0,
        "reason": f"동반 지갑 {peers}개 — 무리({HERD_MIN_PEERS}개 이상)와 단독(0개) 사이다. 근거 없이 분류하지 않는다",
    }


def classify_exit(*, closed_pnl: float | None, kind: str, held_seconds: float | None) -> dict[str, Any]:
    """고래 청산 복기. **고래 자신의** 손익 방향으로 이익 실현/손절을 가른다."""
    if closed_pnl is None:
        outcome = None
        label = "고래 실현 손익 미상 — 이익 실현인지 손절인지 갈리지 않는다"
    elif closed_pnl > 0:
        outcome, label = EXIT_TAKE_PROFIT, "고래 자신은 이익 실현"
    elif closed_pnl < 0:
        outcome, label = EXIT_STOP, "고래 자신은 손절"
    else:
        outcome, label = EXIT_FLAT, "고래 자신은 손익 0"
    return {
        "exit_outcome": outcome,
        "exit_label": label,
        "full_exit": kind == "close",
        "closed_pnl": closed_pnl,
        "held_seconds": held_seconds,
        "estimate": True,
        # C7 — 동시 관측이지 인과가 아니다.
        "note": "고래 청산과 가격 움직임은 같은 창에서 관측된 사실이며 한쪽이 다른 쪽의 원인이라고 말하지 않는다",
    }


def performance_by_type(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """유형별 추종 성적 (사후 채점). **선정 기준에 넣지 않는다**(C8)."""
    buckets: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "net": 0.0, "wins": 0, "profit": 0.0, "loss": 0.0})
    for row in rows:
        kind = str(row.get("entry_type") or TYPE_UNCLASSIFIED)
        net = row.get("net_pnl_usdt")
        if net is None:
            continue
        bucket = buckets[kind]
        bucket["count"] += 1
        bucket["net"] += float(net)
        if float(net) > 0:
            bucket["wins"] += 1
            bucket["profit"] += float(net)
        else:
            bucket["loss"] += abs(float(net))
    result: dict[str, Any] = {}
    for kind, bucket in buckets.items():
        count = int(bucket["count"])
        loss = float(bucket.pop("loss"))
        profit = float(bucket.pop("profit"))
        result[kind] = {
            "count": count,
            "net_usdt": round(float(bucket["net"]), 4),
            "win_pct": round(int(bucket["wins"]) / count * 100, 1) if count else None,
            "profit_factor": round(profit / loss, 2) if loss > 0 else None,
            "sample_note": f"N={count}" + ("" if count >= 30 else " — N<30 이므로 성적으로 단정하지 않는다"),
        }
    total = sum(int(row["count"]) for row in result.values())
    unclassified = int(result.get(TYPE_UNCLASSIFIED, {}).get("count") or 0)
    return {
        "by_type": result,
        "total": total,
        "unclassified_pct": round(unclassified / total * 100, 1) if total else None,
        "not_selection_criteria": "유형별 성적은 사후 채점이며 추종 자격에 넣지 않는다(C8). 사후 지표를 선정에 넣으면 자기실현된다.",
        "estimate": True,
    }


def build_replay(repo: Any, *, limit: int = 500, peer_lookback: int = 2000, cache: Any = None) -> dict[str, Any]:
    """추종 거래별 진입·청산 복기. 저장된 체결만 읽는다(C9).

    `cache` 는 `whale_exit_replay.EventCache` 다. 대조와 복기가 같은 (지갑, 심볼) 이벤트를
    읽으므로 조회를 공유한다 — 같은 요청에서 두 번 읽으면 락 대기가 두 배가 된다.
    """
    from app.paper.whale_exit_replay import EventCache

    cache = cache or EventCache(repo)
    peer_events = repo.list_whale_events(limit=peer_lookback)
    rows: list[dict[str, Any]] = []
    for trade in repo.list_whale_follow_trades(limit=limit):
        evidence = trade.entry_evidence or {}
        wallet = str(evidence.get("whale_address") or "").lower()
        if not wallet:
            continue
        at = _parse(evidence.get("whale_event_at")) or trade.entry_at
        if at is None:
            continue
        direction = trade.direction.value
        herd = herd_context(address=wallet, symbol=trade.symbol, direction=direction, at=at, peer_events=peer_events)
        entry = classify_entry(herd=herd, at=at, price_move_pct=None)
        exits = cache.events(wallet, trade.symbol)
        whale_exit = next(
            (item for item in sorted(exits, key=lambda x: x.event_at) if item.event in {"reduce", "close"} and item.event_at > at),
            None,
        )
        exit_block = (
            classify_exit(
                closed_pnl=_closed_pnl(whale_exit),
                kind=str(whale_exit.event),
                held_seconds=(whale_exit.event_at - at).total_seconds(),
            )
            if whale_exit is not None
            else {"exit_outcome": None, "exit_label": "고래가 아직 청산하지 않았다", "estimate": True}
        )
        rows.append(
            {
                "id": str(trade.id),
                "symbol": trade.symbol,
                "direction": direction,
                "whale_address": wallet,
                "net_pnl_usdt": trade.net_pnl_usdt,
                **entry,
                **exit_block,
            }
        )
    return {"trades": rows, **performance_by_type(rows)}


def _closed_pnl(event: Any) -> float | None:
    payload = getattr(event, "payload", None)
    if not isinstance(payload, dict):
        return None
    for source in (payload.get("payload") if isinstance(payload.get("payload"), dict) else None, payload):
        if not isinstance(source, dict):
            continue
        for key in ("closed_pnl", "closedPnl"):
            raw = source.get(key)
            if raw in (None, ""):
                continue
            try:
                return float(raw)
            except (TypeError, ValueError):
                continue
    return None


def _parse(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
