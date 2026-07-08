# AlertAudit — 포지션 알림 미도달 진단 보고서 (WO-FCE-44 Part A)

사용자 보고: "진입해도, 종료해도 알림이 안 오고, 판정 변화도 안 와서 감으로 익절·손절."
원인 후보 4가지를 실DB(backend/fomo_control_engine.db, alerts 테이블 259행)와 코드로 전수 감사했다.

## 진단 결과 요약

| # | 후보 | 판정 | 증거 |
|---|---|---|---|
| 1 | 엔진 미구현/배선 누락 | **부분 해당** — 조건 알림은 배선됨, `detect_closures`는 스텁 | AlertEngine·워커 훅 존재. `runtime.detect_closures` 주석: "WO-17 can attach alert evaluation" — 종료 알림 미부착 상태 |
| 2 | 발화 조건 미충족 | **부분 해당** | trigger_near 등 259건 발화 — 발화 자체는 정상. 단 status_worsened·health_drop·wyckoff_event는 발화 0건 (관찰 지속 항목) |
| 3 | 발화했으나 미발송 | **주원인 ①** | **259건 중 225건(87%) delivered=0.** 최초 발송 성공 = 2026-07-06 15:51 UTC — 그 전엔 토큰/chat_id 미설정으로 전량 미발송 (같은 날 critical 무효화 이탈 6건 포함). 설정 후 미발송 62건은 전부 KST 01:15~06:23 = 콰이어트 아워 억제분 |
| 3b | 억제분 유실 | **주원인 ②** | 콰이어트 억제분은 아침 요약(08:30)에 병합되는 설계지만 `NotificationState.suppressed_alerts`가 **인메모리** — 백엔드 재시작(최근 며칠간 수차례) 시 통째로 유실. 억제 → 유실 → 사용자에겐 "그냥 안 온" 알림 |
| 4 | 원하는 이벤트가 규칙에 없음 | **주원인 ③ (최대 격차)** | RULE_LABELS 22종 전수 확인 — `position_opened`/`position_closed`/`verdict_changed`/`stance_flipped`/`evidence_insufficient`/`periodic_pulse` 전부 부재. 사용자가 요구한 "진입/종료/판정 변화" 알림은 애초에 존재하지 않았다 |

추가 발견 (Part C 선행 결함): 종료 감지가 **1틱 부재 즉시 auto-close** — sync 간극·거래소
일시 오류로 인한 가짜 종료 기록 위험이 실재했다 (`_sync_bitget_positions`의 missing 루프).

추가 발견 ② (라이브 검증 중 확정): `_alert_context`가 `action_plan`만 있으면 조기 반환해
`chart_analysis`가 영영 컨텍스트에 실리지 않았다 — **wyckoff_event 규칙 발화 0건(DB 증거)과
스탠스 추적 불능의 실원인**. sync 페이로드의 action_plan은 report 기반이라 차트 분석이
없는데도 "완전한 컨텍스트"로 취급된 것. 수정: chart_analysis 부재 시 풀 컨텍스트 조회
(비용: 보유 심볼당 캔들 1회/90s — Bitget 공개 한도 대비 미미).

## 수리 내역 (감사 → 수리 → 증설 순서 준수)

### 수리 (기존 경로)
- **억제분·요약 상태 영속화**: `NotificationState.save/load` (JSON, 원자적 교체) —
  콰이어트 억제분·아침/주간 요약 상태·쿨다운·라이프사이클 트래커가 재시작을 살아남는다.
  경로 `FCE_NOTIFICATION_STATE_PATH` (기본 ./notification_state.json). 워커 기동 시 load.
- **가짜 종료 디바운스**: `Position.sync_miss_count` — `FCE_ALERT_CLOSURE_CONFIRM_TICKS`(기본 2)
  연속 부재 확인 후에만 종료 기록. 재등장 시 카운터 리셋.
- **발송 실패 보증**: 발송 실패분을 `pending_redelivery` 큐에 적재 → 다음 펄스에
  "미도달 알림 n건 병합"으로 도달 보증. `/status`에 "알림 24h: 발화 n · 발송 n · 실패 n" 노출.

### 증설 (Part B — 라이프사이클 6종, `notify/lifecycle.py`)
| 이벤트 | 트리거 | 내용 |
|---|---|---|
| position_opened | sync 신규 감지 | 심볼·방향·진입가 + **즉시 초기 판정**(WO-43 1줄 판정 엔진 소비: "관측 양호: 상방 근거 5/6 · 반대 1" + 최강 모듈 3줄) + 시나리오 매칭 여부 |
| position_closed | 2틱 확인 후 종료 확정 | 실현 손익 + 복기 2줄 (판단 채점 적중/오판/휩쏘 + 실현 R) |
| verdict_changed | WO-40 4상태 전이 | "관찰 유지 → 위험 확인 필요" + 사유 1줄 + 다음 가격 (악화=warn, 완화=info) |
| stance_flipped | 종합 스탠스 상방↔하방 | 반전 근거 상위 1개 (1줄 판정 최강 모듈) |
| evidence_insufficient | standby 또는 판정 가능 모듈 <3, 2h 지속 | "판단 근거 부족 — 구조 형성 대기" |
| periodic_pulse | 보유 중 4h(config) | 포지션별 1줄 묶음 1통. **"전부 정상"도 발송** — 침묵과 고장의 구분. 무음 준수(억제 시 아침 요약 병합) |

- 기존 규칙 22종 유지, 위 6종은 기본 활성(`FCE_ALERT_RULES_ENABLED`에 추가).
- 워커 훅 순서: detect_closures → **evaluate_lifecycle** → evaluate_alerts → performance → **periodic_pulse** → daily_summary.

### 판단 문형 가드
매매 지시·권유 문형("진입하세요" 류) 금지 — 전 메시지가 관측 서술형.
`tests/test_lifecycle_alerts.py`가 notify/ 소스 전체와 생성 메시지를 grep으로 강제 (위반 시 테스트 실패).

## 수용 기준 대응

- [x] 진단 보고서 (이 문서) — 원인 분류 3주원인 + 수리 내역
- [x] 신규 진입 시 opened 알림 + 초기 판정: sync 주기(90s) 내 발화 — 1분 내 요건 충족 (E2E 테스트)
- [x] 종료 시 복기 요약 + 가짜 종료 오탐 테스트 (`test_bitget_sync_auto_records_missing_position_exit` 2틱 시나리오)
- [x] 4h 펄스 무음 준수 (`test_pulse_respects_quiet_hours`) + 간격 게이트
- [x] 금지 문형 grep 0 (소스·메시지 이중 검증)
- E2E: `test_e2e_lifecycle_sequence` — open → verdict change → close가 페이크 싱크에 순서대로 도착

## 운영 체크리스트 (실계정 검증용)

1. 백엔드(8875) 재시작 — 새 규칙·훅 반영 (오토리로드 없음).
2. `FCE_TELEGRAM_BOT_TOKEN`/`FCE_TELEGRAM_CHAT_ID` 설정 확인 (2026-07-06 15:51 UTC부터 정상 — 현재 OK).
3. `/status`로 "알림 24h" 라인 확인 — 발화 대비 실패가 크면 토큰/네트워크 점검.
4. 신규 진입 후 90초 내 opened 알림 수신 확인.
