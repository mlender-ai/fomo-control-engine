# 포지션 알림 침묵 (Alert Silence)

> **증상**: 포지션을 잡았는데 텔레그램 알림이 오지 않고 관측도 되지 않는다.
> 정본 코드: `app/notify/lifecycle.py` · `app/notify/alerts.py` · `app/services/http_handlers.py`

---

## 1. 원인 — 하나의 예외가 두 곳을 죽였다

```python
services/http_handlers.py:1168
def _live_position_payload(position, store_snapshot=False):
    report = _generate_and_store_report(position.symbol)   # ← 거래소 스냅샷. 던진다
```

`_generate_and_store_report()` 는 `market_provider.get_snapshot()` 을 타므로 **신규 상장·
캔들 부족·레이트리밋·거래소 일시 오류에 `HTTPException(422)` 을 던진다.**

그 하나가 두 경로를 동시에 끊었다:

| 경로 | 코드 | 결과 |
| --- | --- | --- |
| 관측 | `sync_live_positions` → `except HTTPException: continue` | `positions` 에서 빠짐 — 펄스·구조 알림·전이 알림 전멸 |
| 진입 알림 | `evaluate_lifecycle` → `except Exception: continue` | `position_opened` 소멸 |

**둘 다 아무것도 남기지 않았다.** `created` 카운터는 포지션이 생겼다고 말하는데
`positions` 에는 없고 알림도 없다. 원장에도 안 남아서 **침묵과 고장이 구분되지 않았다.**

> 이 저장소는 같은 원칙을 이미 적었다 — `ENGINE-LIVENESS-01` D1:
> **"생존 신호가 데이터 수집 성공에 의존하면 안 된다."**
> 진입 사실도 같은 종류의 1차 정보다. 판정·시나리오는 부가 정보다.

---

## 2. 수리

### 2-1. 진입 알림은 부가 정보에 종속되지 않는다

컨텍스트 조회가 실패하면 **원장 행만으로** 최소 알림을 낸다:

```
🟢 진입 감지 · NEWCOINUSDT 롱 5x @ 0.012340
⚠️ 초기 판정 미첨부 — 부가 정보 조회 실패 (HTTPException)
진입 사실은 확정이다. 판정·시나리오는 다음 주기에 붙는다.
판단은 사용자 몫입니다. 주문 실행 없음.
```

**무엇이 빠졌는지 본문에 적는다.** 조용히 축약하면 "판정이 없다"와 "판정을 못 읽었다"가
구분되지 않는다.

`minimal_position_payload()` 는 **네트워크를 타지 않는다** — 대체 경로가 같은 이유로
실패하면 대체가 아니다. 회귀가 그것을 grep 으로 고정한다.

### 2-2. 관측에서 조용히 사라지지 않는다

`sync_live_positions` 가 빠진 포지션을 사유와 함께 남긴다:

```json
{"positions_unavailable": [{"id": "...", "symbol": "NEWCOINUSDT", "reason": "422: ..."}],
 "positions_unavailable_count": 1}
```

그리고 **펄스가 그것을 싣는다**:

```
📡 정기 상태 펄스 · 기준 08-31 18:00
관측 가능한 보유 포지션 없음.
⚠️ NEWCOINUSDT 관측 불가 — 분석 조회 실패(422: candles unavailable). 포지션은 열려 있다.
```

싣지 않으면 **"보유 포지션 없음 — 감시 정상"이 열린 포지션 위에서 찍힌다.**
침묵이 정상으로 위장되는 것이 이 결함의 가장 나쁜 부분이었다.

---

## 3. 이 수리가 덮지 못하는 경우

알림이 안 오는 원인은 이것 하나가 아니다. 순서대로 갈라야 한다:

| 확인 | 무엇을 보나 |
| --- | --- |
| 워커가 도는가 | `sync_and_analyze` 하트비트 · `last_effective_run_at` |
| 실행 커밋 | `git rev-parse HEAD` == `origin/main` — **머지는 배포가 아니다** |
| 거래소 연결 | `sync_live_positions` 의 `status` — `not_configured`·`not_active`·`permission_error` 면 포지션 자체가 안 잡힌다 |
| 발송 설정 | `FCE_TELEGRAM_ALERTS_ENABLED` · `alert_enabled_rule_set` 에 `position_opened` |
| 조용 시간 | 기본 **01:00~08:00 KST**. `critical` 이 아니면 억제되고 다음 펄스에 병합된다 |
| 뮤트 | `state.is_muted()` |

**`status` 가 `not_configured` 면 이 수리와 무관하다** — 그때는 포지션이 원장에 생기지도
않으므로 알릴 대상이 없다. 그 경우도 침묵이므로 별건으로 봐야 한다.
