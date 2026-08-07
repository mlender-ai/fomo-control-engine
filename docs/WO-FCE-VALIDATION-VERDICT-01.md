# WO-FCE-VALIDATION-VERDICT-01 — 측정에서 판정으로

우선순위: P0
실사 기준: main @ `cc3ccd0`
선행: `WO-FCE-OBSERVATION-INTEGRITY-01`, `WO-FCE-SAMPLE-VIABILITY-01`

## 0. 이 WO의 위치

지난 두 WO로 **측정 장치는 완성**됐다. 그런데 그 장치가 산출한 숫자가 아직 사용자에게
발행되지 않았고, 세 가지가 사람의 결정을 기다리고 있었다.

> 목적은 새 기능이 아니라 **"검증을 완주할 수 있는가"에 숫자로 답하고, 완주 이후를 미리
> 확정하는 것**이다.

## 1. 실행 순서와 그 이유

```
Phase 4 (게이트 문서) → Phase 1 (판정 발행) → Phase 2 (호스트) → Phase 3 (조건부) → Phase 5
```

Phase 4 가 먼저인 이유는 **C2** 다 — 결과를 보기 전에 기준을 정한다. 커밋 순서가 증명이다.

| 커밋 | Phase | 내용 |
| --- | --- | --- |
| `f9b2dcd` | 4 | 자동매매 게이트 사전 확정 |
| `079f22e` | 1 | 완주 판정 발행 + 전이 알림 |
| `d72300a` | 2 | 절전 방지 자가 점검 |
| `d7709fa` | 5 | 제헌절 확정 + 정산 지연 실측 |

## 2. Phase별 산출물

### Phase 4 — 자동매매 전환 게이트 (P0, 전략적 핵심)

정본: [`docs/validation/LIVE_TRADING_GATE.md`](validation/LIVE_TRADING_GATE.md)
판정 코드: `app/validation/live_trading_gate.py::live_trading_readiness`

6축 + 안전장치(자본·포지션·일손실 상한, 킬 스위치 4조건, 페이퍼 병행)를 명문화했다.
**판정 코드는 아무것도 해제하지 않는다** — 반환값에 해제 경로가 없고 설정을 쓰지 않는다.
`GATE_APPROVED = False` 가 사용자 서명 전까지 게이트 전체를 미충족으로 고정한다.

봉인(C5) 불변: `LiveBroker` 는 Protocol(타입 계약)이며 구현체가 없다. 실제 봉인은
`create_broker` 의 `RuntimeError` 와 `Settings` 검증이고, 둘 다 테스트로 고정했다.

### Phase 1 — 완주 가능성 판정 발행

- **판정별 대응 규칙을 결과 전에 고정**했다(`verdict_watch.VERDICT_ACTIONS`).
  네 판정 어디에도 "기준을 낮춘다"가 없다.
- 주간 성과 리포트에 트랙별 판정 + 유효일 + 표본 + **D+28 예상 표본** 상시 포함.
- 판정이 바뀔 때만 알림 1건. 좋아지는 전이도 알린다. 첫 관측은 전이가 아니다(초기화다).

### Phase 2 — 절전 방지 자가 점검

`app/worker/sleep_guard.py` + `scripts/local/check-sleep-guard.sh`.
전원 설정과 `caffeinate` wake lock 을 **함께** 본다 — 둘 다 없을 때만 위험이다.
미적용이면 진단 API `manual_actions` 에 `sleep_guard_off` 가 상시 올라온다.

> **감지 없는 조치는 조치가 아니다.** 설정이 풀리면 아무 소리 없이 손실이 다시 시작된다.

### Phase 3 — `STRUCTURALLY_BLOCKED` 트랙 처리

**착수하지 않았다.** WO 규정: "판정이 없으면 착수 금지."
Phase 1 판정은 운영 DB 에서 나오는데 이 세션에서 그 DB 에 접근할 수 없다(§3).
판정이 나온 뒤 해당 트랙에 대해서만 실행한다.

### Phase 5 — 미결 항목 청산

1. **2026-07-17 제헌절 — 확정.** 2026-04-28 국무회의 의결로 재지정, KRX 전 시장 휴장.
   `KR_CONFIRMED` 로 이동했다.
2. **`settlement_buffer_days` 재측정 경로** — 리포트가 만기→정산확정 지연 실측(N·중앙값·최대)을
   현재 설정값과 나란히 낸다. 표본 0이면 "재측정 불가"라고 쓰고 숫자를 만들지 않는다.
3. 호스트 안 선택 → Phase 2 의 비교표와 즉시 조치까지. **선택은 사람이 한다.**

## 3. 이 세션이 산출할 수 없었던 것

**운영 SQLite 는 사용자 호스트에 있고 이 실행 환경에서 접근할 수 없다.** 따라서 아래는
장치까지만 만들었고 숫자는 사용자가 한 번 실행해야 나온다. 없는 숫자를 문서에 적지 않았다.

| 항목 | 산출 명령 |
| --- | --- |
| 4트랙 판정 + D+28 예상 표본 | `cd backend && python3 scripts/sample_viability_report.py ~/fomo_control_engine.db` |
| 게이트 6축 진행도 | `curl -s localhost:8875/api/system/paper/diagnosis \| python3 -m json.tool \| grep -A 80 live_trading_gate` |
| 절전 방지 상태 | `scripts/local/check-sleep-guard.sh` |
| 호스트 리소스 (C안 검토용) | `FCE_SAMPLES=360 scripts/local/measure-resources.sh` |
| 텔레그램 실수신 | 주간 리포트 발송 시각 도래 또는 판정 전이 발생 시 |

`/validation` 화면에도 같은 값이 뜬다.

## 4. 수용 기준

- [x] 판정별 대응 규칙이 문서에 고정 (결과 확인 **전** 커밋 — `079f22e` 이전에 규칙 확정)
- [x] 주간 리포트에 판정·D+28 예상 표본 포함, 판정 전이 시 알림 1건
- [ ] 4트랙 실측표 — **장치 완성, 실행은 호스트에서** (§3)
- [ ] 텔레그램 실수신 스크린샷 — 발송 시각 도래 필요 (§3)
- [x] 절전 방지 자가 점검 + 미적용 시 경고 동작
- [ ] 즉시 조치 적용 후 US 후반 커버리지 전후 대조 — **호스트 실행 필요** (§3)
- [x] `docs/validation/LIVE_TRADING_GATE.md` — 6축 + 안전장치 + 사용자 승인 서명란
- [x] `live_trading_readiness()` + `/validation` 대시보드 진행도
- [x] `LiveBroker` 미구현 확인 + `live_trading_enabled=true` 기동 실패 유지
- [x] 게이트 문서가 Phase 1 판정 발행 **전에** 커밋됨 (커밋 순서: `f9b2dcd` → `079f22e`)
- [x] 제헌절 확인 결과 반영
- [x] 정산 지연 재측정 경로 + 표본 부족 시 명시
- [x] 임계값·게이트 파라미터 diff 0 (C1)
- [x] 주문 실행 봉인 불변 (C5)

## 5. 사람이 결정해야 하는 것

1. **자동매매 게이트 서명** — `docs/validation/LIVE_TRADING_GATE.md` §5.
   축별 임계(제안 40% / 95% / 14일 / 1일), 안전장치 4개, 트랙별 개별 서명.
   서명 후 `live_trading_gate.py::GATE_APPROVED` 를 뒤집어야 게이트가 통과 가능해진다.
2. **호스트 지속성 안 선택** (A/B/C/D) — `docs/validation/HOST_PERSISTENCE.md` §3.
3. **축 2 벤치마크 배선 여부** — 주식·폴리는 대조군이 정의되지 않아 축 2가 영구 미충족이다.
   지수 프록시를 승패 시퀀스로 바꿀지, Brier 대조군을 만들지는 설계 결정이다.

## 6. 금지 (이 WO 에서 하지 않은 것)

- `LiveBroker` 구현 (C5)
- 진입 게이트·검증 완료 기준(28일/30표본/2국면)·통계 임계 완화 (C1)
- Phase 1 판정 없이 Phase 3 착수
- 결과를 본 뒤 게이트 축·임계 조정 (C2)
- 호스트 지속성 안 **선택**
