# 적용된 임시값 (WO-FCE-DEFAULTS-01)

구현: `backend/app/validation/provisional_defaults.py`

## 원칙

> **화면과 파이프라인을 돌리는 임시값은 넣는다. 측정을 거짓말하게 만드는 임시값은 넣지 않는다.**

| 넣었다 | 넣지 않았다 |
| --- | --- |
| 고래 추종 시작 자본 500 USDT | 체결 invariant 완화 |
| 폴리 처리 방침 = 제외 | 유실일을 유효일 분모에서 제외 |
| 지연·이탈 상한 확정 | 표본 미달을 충분으로 표기 |
| 판정 모듈 분모 정합 수리 | 게이트 임계 하향 |
| KR 큐 주문 보류 | |

**두 번째 열은 임시값이 아니라 조작이다.** 하면 두 달의 계측이 전부 무의미해진다.
`test_no_measurement_altering_default_is_listed` 가 그 넷이 목록에 오르지 않음을 고정한다.

## 적용 목록 (실측 2026-08-27)

| # | 항목 | 값 | 원복 |
| --- | --- | --- | --- |
| 1-1 | 고래 추종 시작 자본 · 동시 보유 상한 | **500 USDT · 5건** | `FCE_WHALE_FOLLOW_STARTING_CAPITAL_USDT=0`<br>`FCE_WHALE_FOLLOW_MAX_OPEN_POSITIONS=0` |
| 1-2 | 폴리마켓 검증 대상 제외 | **A안 · 수집·원장 유지** | `FCE_VALIDATION_EXCLUDE_POLY=false` |
| 1-3 | 추종 진입 지연·이탈 상한 | **30분 · 25%** | `FCE_WHALE_FOLLOW_MAX_LATENCY_MINUTES`<br>`FCE_WHALE_FOLLOW_MAX_DRIFT_PCT_OF_STOP` |
| 1-4 | KR 주식 큐 주문 보류 | **세션 개장 시 보류** | `FCE_STOCK_PAPER_HOLD_QUEUED_ORDERS=false` |

`applied_defaults()` 는 **설정을 읽어** 실제 상태를 낸다 — 문서를 믿지 않는다. 원복하면
목록에서 자동으로 빠진다(`test_defaults_disappear_when_reverted`).

원복 방법이 없는 항목은 목록에 오를 수 없다 — `_REQUIRED_KEYS` 검사가 실행 시점에 막는다.
**원복 불가한 임시값은 임시값이 아니라 확정값이다.**

## 1-4 가 invariant 를 건드리지 않는 이유

```
execution.py:53  base = observation.session_open_price     (세션 시가로 체결가 산출)
execution.py:59  minute_low <= fill_price <= minute_high   (현재 분봉으로 검사)
```

서로 다른 봉이므로 시가 대비 가격이 반 스프레드 이상 움직이면 위반이 **반드시** 난다.
US 가 그렇게 이미 정지했고 KR 큐 **13,836건**이 같은 실패를 대기 중이다.

**정지를 막는 것이 아니라 정지를 유발할 주문을 보내지 않는다.** invariant 는 그대로다 —
`test_queue_hold_does_not_touch_the_invariant` 가 고정한다. 근본 수리(봉 불일치)는 별건이다.

보류는 **세션이 열렸을 때만** 한다. 닫혀 있으면 어차피 `session_closed` 로 되돌아오므로
막을 것이 없고, 막으면 관측 기록만 사라진다.

## 확정하는 방법

사용자가 값을 확정하면 이 문서와 `provisional_defaults.py` 에서 항목을 **뺀다.**
빼는 행위가 곧 확정 기록이다. `pending_decisions` 의 해당 항목도 그때 지운다 —
지금은 `provisional_applied` 등급으로 남아 있다(삭제 0건).
