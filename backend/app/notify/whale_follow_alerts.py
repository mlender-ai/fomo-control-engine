"""고래 추종 트랙 알림 — WO-FCE-WHALE-FOLLOW-01 6-3 (상한) · **-02 7-3 (문구 단순화)**.

## 스팸 사고를 재발시키지 않는다 (C7)

`WHALE-ALERT-DEMOTE-01` 이 `whale_entry` 를 강등한 이유는 빈도가 아니라 **기전**이었다:

```
state_key = rule_id : position_id : identity
identity 에 체결 ID·건수가 들어갔다
  → 배치가 바뀔 때마다 새 키가 생긴다
  → 쿨다운이 조회하는 키가 매번 달라진다
  → 상한이 구조적으로 존재하지 않았다   (같은 지갑 6건이 같은 분에 도착)
```

그래서 이 모듈의 `identity` 는 **배치 내용에 의존하지 않는다.** 지갑·심볼·방향·단계
네 축만 쓴다. 같은 지갑이 같은 심볼에 같은 방향으로 다시 진입해도 키가 같으므로 쿨다운이
실제로 걸린다.

그리고 쿨다운에 더해 명시적 상한을 처음부터 넣는다 — 지갑당 시간당, 실행당 총량.
상한은 **원장에서 계산한다.** 별도 상태 저장소를 두면 그것이 드리프트하고, 드리프트한
상태는 상한이 없는 것과 같다.

## `whale_entry` 와 다른 rule 이다

`whale_entry`(강등 유지)는 **고래 체결 자체**의 알림이었다. 이 rule 은 **엔진이 그 체결을
보고 가상 진입을 했다**는 알림이다. 후자는 우리 행동이므로 알 가치가 있다.

## 문구는 자격만큼 단순해졌다 (WO-FCE-WHALE-FOLLOW-02 7-3)

Phase 6 은 모든 건이 `미검증 · 승격 아님 · CI 하한 35.9%` 로 똑같이 찍혀서 **라벨이 정보를
주지 못했다.** 자격이 `N>=30 · 승률>=55% · MM 아님` 하나로 줄었으므로 본문도 그 규칙을
그대로 읽게 만든다:

```
고래 0x10f1…202f · 승률 64.9% (N=37) · directional
ETHUSDT long · 진입 2478.1 · 무효화 2425.59
체결→진입 3.2분 · 이탈 0.4%
실주문이 아닌 엔진 가상 거래 기록입니다.
```

**승률과 지연·이탈이 본문에 있다.** 그 셋이 이 트랙이 무엇을 했는지의 전부다.

## 청산 알림에 결과를 붙인다 (7-3 항목 2)

진입과 청산이 **짝으로** 보여야 한다. `identity` 가 지갑·심볼·방향·단계로 같으므로
`opened`/`closed` 가 같은 축에 놓이고, 청산 본문에 진입가·순손익·사유가 함께 나온다.

## 미검증임은 계속 박는다 (C8)

추종 자격은 승격이 아니다. 그리고 실주문이 아님을 매번 적는다 — 이 트랙은 페이퍼다(C1).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.db.models import PaperTrade
from app.notify.rules import AlertCandidate

WHALE_FOLLOW_RULE_ID = "whale_follow_entry"

# 지갑당 시간당 상한. 한 지갑이 여러 심볼에 동시에 들어가도 알림은 이 수를 넘지 않는다.
PER_WALLET_HOURLY_LIMIT = 3
# 실행당 총 상한. 여러 지갑이 동시에 움직여도 한 번에 이 수를 넘기지 않는다.
PER_RUN_LIMIT = 5
# 발송 대상 단계. 후보·관측·거부는 조회 대상이지 발송 대상이 아니다(화이트리스트 원칙).
SENDABLE_PHASES = ("opened", "closed")


def alert_identity(*, address: str, symbol: str, direction: str, phase: str) -> str:
    """`state_key` 의 가변 부분. **배치 내용이 들어가지 않는다.**

    체결 ID·건수·시각·금액은 여기 없다. 그것들이 들어간 것이 스팸 사고의 기전이었다.
    """
    return f"{address.lower()}:{symbol.upper()}:{direction}:{phase}"


def _evidence(trade: PaperTrade) -> dict[str, Any]:
    return trade.entry_evidence or {}


def _latency_label(seconds: Any) -> str:
    if not isinstance(seconds, (int, float)):
        return "지연 미측정"
    value = float(seconds)
    if value < 60:
        return f"체결→진입 {value:.0f}초"
    if value < 3600:
        return f"체결→진입 {value / 60:.1f}분"
    return f"체결→진입 {value / 3600:.1f}시간"


def _drift_label(pct: Any) -> str:
    """고래 체결가 대비 불리한 이탈. **미측정과 0% 를 구분한다.**"""
    if not isinstance(pct, (int, float)):
        return "미측정"
    return f"{float(pct):.2f}%"


def _pct(value: Any) -> str:
    return f"{float(value):.1f}%" if isinstance(value, (int, float)) else "미상"


def format_message(trade: PaperTrade, *, phase: str) -> str:
    """본문 필수 항목 (7-3 항목 1). 자격 규칙을 그대로 읽을 수 있어야 한다.

    승률·N·유형이 **자격 근거**이고, 지연·이탈이 **추종이 성립했는지**의 근거다. 하나라도
    빠지면 "왜 이 거래를 했는가"를 알림만 보고 되짚을 수 없다.
    """
    evidence = _evidence(trade)
    kind = str(evidence.get("participant_type") or "unclassified")
    win_pct = evidence.get("win_pct")
    sample = evidence.get("sample_size")
    address = str(evidence.get("whale_address") or "")
    short = f"{address[:6]}…{address[-4:]}" if len(address) > 12 else address

    lines = [
        f"고래 {short} · 승률 {_pct(win_pct)} (N={sample if sample is not None else '미상'}) · {kind}",
        f"{trade.symbol} {trade.direction.value} · 진입 {trade.entry_price:g} · 무효화 {trade.invalidation_price:g}",
        f"{_latency_label(evidence.get('signal_to_entry_seconds'))} · 이탈 {_drift_label(evidence.get('price_drift_pct'))}",
    ]
    if phase == "closed":
        # 7-3 항목 2 — 진입과 청산이 짝으로 보여야 한다. 결과 없는 청산 알림은 반쪽이다.
        lines.append(f"청산 · 순손익 {trade.net_pnl_usdt:+.2f} USDT · {trade.exit_reason or '사유 미상'}")
    # C8 — 추종 자격은 승격이 아니다. 이 줄이 없으면 검증된 신호로 읽힌다.
    lines.append("미검증 추종 자격 (승격 아님)")
    # C1 — 매번 적는다. 이 트랙은 페이퍼이며 실주문 경로는 봉인돼 있다.
    lines.append("실주문이 아닌 엔진 가상 거래 기록입니다.")
    return "\n".join(lines)


def _phase_of(trade: PaperTrade) -> str | None:
    if trade.status == "closed":
        return "closed"
    if trade.status == "open":
        return "opened"
    return None


def build_candidates(
    trades: list[PaperTrade],
    *,
    now: datetime,
    recent_trades: list[PaperTrade] | None = None,
    per_wallet_hourly_limit: int = PER_WALLET_HOURLY_LIMIT,
    per_run_limit: int = PER_RUN_LIMIT,
) -> dict[str, Any]:
    """발송 후보와 **차단 사유**를 함께 낸다(C10).

    `recent_trades` 는 지갑당 시간당 상한을 세는 모집단이다. 원장에서 온다 — 별도 상태
    저장소를 두지 않는다.
    """
    window_start = now - timedelta(hours=1)
    # 지금 평가 중인 거래는 기준선에서 뺀다. 호출부가 원장 전체를 `recent_trades` 로 넘기므로
    # 빼지 않으면 이 거래가 자기 자신을 세어 상한이 의도보다 1 작아진다.
    evaluating = {str(trade.id) for trade in trades}
    hourly: dict[str, int] = {}
    for trade in recent_trades or []:
        if str(trade.id) in evaluating:
            continue
        address = str(_evidence(trade).get("whale_address") or "").lower()
        stamp = trade.entry_at or trade.entry_bar_at
        if address and stamp is not None and stamp >= window_start:
            hourly[address] = hourly.get(address, 0) + 1

    candidates: list[AlertCandidate] = []
    blocked: list[dict[str, Any]] = []
    for trade in trades:
        evidence = _evidence(trade)
        if str(evidence.get("track") or "") != "whale_follow":
            blocked.append({"id": str(trade.id), "reason": "추종 트랙 거래가 아니다"})
            continue
        phase = _phase_of(trade)
        if phase not in SENDABLE_PHASES:
            blocked.append({"id": str(trade.id), "reason": f"발송 대상 단계가 아니다 (status={trade.status})"})
            continue
        address = str(evidence.get("whale_address") or "").lower()
        if not address:
            blocked.append({"id": str(trade.id), "reason": "고래 식별 불가 — 본문 필수 항목을 채울 수 없다"})
            continue
        if len(candidates) >= max(0, int(per_run_limit)):
            blocked.append({"id": str(trade.id), "reason": f"실행당 상한 {per_run_limit}건 도달", "cap": "per_run"})
            continue
        if hourly.get(address, 0) >= max(0, int(per_wallet_hourly_limit)):
            blocked.append(
                {"id": str(trade.id), "reason": f"지갑당 시간당 상한 {per_wallet_hourly_limit}건 도달", "cap": "per_wallet_hourly", "address": address}
            )
            continue
        hourly[address] = hourly.get(address, 0) + 1
        qualification = str(evidence.get("qualification") or "unknown")
        candidates.append(
            AlertCandidate(
                rule_id=WHALE_FOLLOW_RULE_ID,
                severity="info",
                position_id=None,
                symbol=trade.symbol,
                identity=alert_identity(address=address, symbol=trade.symbol, direction=trade.direction.value, phase=phase),
                title=f"고래 추종 {'진입' if phase == 'opened' else '청산'} · {trade.symbol}",
                message=format_message(trade, phase=phase),
                payload={
                    "track": "whale_follow",
                    "phase": phase,
                    "qualification": qualification,
                    # 추종 자격은 승격이 아니다 — 어떤 자격으로 들어왔든 미검증이다.
                    "unverified": True,
                    "whale_address": address,
                    "win_pct": evidence.get("win_pct"),
                    "sample_size": evidence.get("sample_size"),
                    "ci_low": evidence.get("ci_low"),
                    "participant_type": evidence.get("participant_type"),
                    "unclassified_flag": evidence.get("unclassified_flag"),
                    "signal_to_entry_seconds": evidence.get("signal_to_entry_seconds"),
                    "price_drift_pct": evidence.get("price_drift_pct"),
                    "price_drift_pct_of_stop": evidence.get("price_drift_pct_of_stop"),
                    # 7-3 항목 2 — opened/closed 를 짝으로 추적하는 키. identity 와 같은 축이다.
                    "pair_key": alert_identity(address=address, symbol=trade.symbol, direction=trade.direction.value, phase="*"),
                    "net_pnl_usdt": trade.net_pnl_usdt if phase == "closed" else None,
                    "trade_id": str(trade.id),
                    "paper_only": True,
                },
            )
        )
    return {
        "candidates": candidates,
        "blocked": blocked,
        "caps": {"per_wallet_hourly": int(per_wallet_hourly_limit), "per_run": int(per_run_limit)},
        "rule_id": WHALE_FOLLOW_RULE_ID,
        "note": "identity 는 지갑·심볼·방향·단계만 쓴다 — 배치 내용에 의존하지 않는다(C7)",
    }
