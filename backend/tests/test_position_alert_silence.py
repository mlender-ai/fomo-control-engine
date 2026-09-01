"""포지션은 잡혔는데 텔레그램이 조용했던 원인 — 침묵 경로 두 개.

## 무엇이 끊겼나

`_live_position_payload()` 는 첫 줄에서 `_generate_and_store_report()` 를 부르고, 그것은
거래소 스냅샷을 타므로 **신규 상장·레이트리밋·일시 오류에 `HTTPException` 을 던진다.**
그 하나가 두 곳을 동시에 죽였다:

```
sync_live_positions      except HTTPException: continue   → positions 에서 빠짐
                                                            (관측·펄스·구조 알림 전멸)
evaluate_lifecycle       except Exception: continue        → 진입 알림 소멸
```

**둘 다 아무것도 남기지 않았다.** 포지션은 열려 있는데 알림도 관측도 없고, 그 사실조차
조회되지 않았다 — 침묵과 고장이 구분되지 않는 상태다.

> 이 저장소는 같은 원칙을 이미 한 번 적었다: **생존 신호가 데이터 수집 성공에 의존하면
> 안 된다**(`ENGINE-LIVENESS-01` D1). 진입 사실도 같은 종류의 1차 정보다.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.notify.lifecycle import degraded_opened_candidate, pulse_candidate

REPO_ROOT = Path(__file__).resolve().parents[2]


def _position() -> dict:
    return {
        "id": str(uuid4()),
        "symbol": "NEWCOINUSDT",
        "direction": "long",
        "leverage": 5,
        "entry_price": 0.01234,
        "opened_at": "2026-08-31T09:00:00Z",
    }


# ── 침묵 경로 ① 진입 알림 ───────────────────────────────────────────────


def test_entry_alert_survives_a_context_failure() -> None:
    """**진입 사실은 1차 정보다.** 부가 정보 조회 실패가 그것을 삼키면 안 된다."""
    candidate = degraded_opened_candidate(_position(), reason="HTTPException")

    assert candidate.rule_id == "position_opened", "규칙이 바뀌면 발송 화이트리스트를 못 지난다"
    assert "진입 감지" in candidate.message
    assert "NEWCOINUSDT" in candidate.message
    assert "5x" in candidate.message and "0.012340" in candidate.message


def test_degraded_alert_says_what_is_missing() -> None:
    """조용히 축약하면 "판정이 없다"와 "판정을 못 읽었다"가 구분되지 않는다."""
    candidate = degraded_opened_candidate(_position(), reason="HTTPException")

    assert "미첨부" in candidate.message
    assert "HTTPException" in candidate.message
    assert candidate.payload["degraded"] is True
    assert candidate.payload["degraded_reason"] == "HTTPException"


def test_alerts_no_longer_drop_the_entry_on_failure() -> None:
    """이전에는 `logger.warning` 뒤 `continue` 하나였다 — 알림이 통째로 사라졌다."""
    source = (REPO_ROOT / "backend/app/notify/alerts.py").read_text(encoding="utf-8")
    block = source.split('if "position_opened" in enabled:')[1].split('if "position_closed" in enabled:')[0]

    assert "degraded_opened_candidate" in block, "실패 시 최소 알림 경로가 없다"
    assert "minimal_position_payload" in block


def test_minimal_payload_does_not_touch_the_network() -> None:
    """대체 경로가 같은 이유로 실패하면 대체가 아니다 — 원장 행만 읽는다."""
    source = (REPO_ROOT / "backend/app/services/runtime.py").read_text(encoding="utf-8")
    block = source.split("def minimal_position_payload")[1].split("\ndef ")[0]

    assert "get_position" in block
    for network in ("_generate_and_store_report", "_live_position_payload", "get_snapshot", "build_action_plan"):
        assert network not in block, f"대체 경로가 네트워크를 탄다: {network}"


# ── 침묵 경로 ② 관측 ────────────────────────────────────────────────────


def test_unavailable_positions_are_recorded_not_dropped() -> None:
    """`continue` 만 있으면 포지션이 관측에서 **조용히** 사라진다."""
    source = (REPO_ROOT / "backend/app/services/http_handlers.py").read_text(encoding="utf-8")
    block = source.split("def sync_live_positions")[1].split("\ndef ")[0]

    assert "positions_unavailable" in block
    assert "positions_unavailable_count" in block
    # 사유가 남아야 "왜 못 봤는지"를 되짚을 수 있다.
    assert '"reason"' in block


def test_pulse_surfaces_the_observation_gap() -> None:
    """**"전부 정상"이 열린 포지션 위에서 찍히면 안 된다.**"""
    candidate = pulse_candidate([], unavailable=[{"symbol": "NEWCOINUSDT", "reason": "422: candles unavailable"}])

    assert candidate is not None
    assert "관측 불가" in candidate.message
    assert "NEWCOINUSDT" in candidate.message
    assert "포지션은 열려 있다" in candidate.message
    assert "감시 정상 동작 중입니다" not in candidate.message, "관측 공백 위에 정상 문구가 찍혔다"


def test_pulse_still_says_normal_when_there_is_no_gap() -> None:
    """대조 — 공백이 없으면 문구가 그대로다. 회귀가 아니다."""
    candidate = pulse_candidate([], unavailable=[])

    assert candidate is not None
    assert "감시 정상 동작 중입니다" in candidate.message


def test_pulse_signature_is_backward_compatible() -> None:
    """기존 호출부가 `unavailable` 없이도 돌아야 한다."""
    assert pulse_candidate([]) is not None
