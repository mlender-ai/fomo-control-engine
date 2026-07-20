# 판단 원장 커버리지

WO-FCE-89 정본. 해자의 단위는 화면 기능이 아니라 **당시 가격·근거·파라미터가 박힌 판단과 사후 결과**다. 운영 원장은 기존 `judgment_ledger` / `judgment_scores`이며, Toss의 `scout_judgment_snapshots` / `scout_judgment_outcomes`는 T+1·5·20 결과 저장소로만 사용한다. 같은 판단을 제3의 원장에 복제하지 않는다.

## 커버리지 정의

최근 7일의 커버리지는 다음과 같다.

```text
원장에 기록된 채점 가능 판단 / (전체 판단 - 결과 정의가 없는 채점 불가 기록)
```

- `pending`: 원장 행은 존재하지만 정해진 horizon 또는 포지션 종료가 아직 오지 않음.
- `unscorable`: 방향 주장이 아닌 사실·전달·수명주기 기록. 억지 outcome을 만들지 않는다.
- `unclassified`: 코드에 새 판단 유형이 추가됐지만 이 문서/registry에 정의되지 않음. 커버리지 상태는 `attention`이 된다.
- 자율 상태 전이는 `autonomy_logs`가 append-only 정본이다. generic ledger에 복제하지 않고 채점 불가 분모 제외로 명시한다.

## 판단 유형 인벤토리

| 판단 유형 | 발행 지점 | 분류 | 채점 정의 |
|---|---|---|---|
| 방향 flip (`stance_flipped`) | lifecycle alert → semantic ledger row | 기록 중 | 다음 flip/종료까지 held 방향의 net 수익 |
| 스카우트 후보·승격 근거 (`candidate_signature`, `universe_discovery`) | scout/validation | 기록 중 | T+24h 방향 및 signature별 outcome |
| 진입 기회 (`scout_setup`, `entry_intent`, `entry_checklist`) | scout monitor/scenario | 기록 중 | 조건 발화 후 T+N 또는 종료 시 R 결과 |
| 포지션 상태 전이 (`position_status_transition`) | snapshot status change / verdict alert | 기록 중 | 다음 24h 스냅샷 또는 종료 시 위험 방향 변화; 도래 전 pending |
| 포지션 건강점수 변화 (`position_health_change`) | snapshot event | 채점 불가 | 독립 방향 주장이 아닌 상태 사실 |
| 익절·무효화 (`take_profit`, `invalidation`, `planned_*`) | action plan/scenario | 기록 중 | 종료 전 선행 터치 및 보수적 same-bar 규칙 |
| 고래 추종 관측 | `candidate_signature(engine=whale)` | 기록 중 | T+24h 방향; 미검증 고래는 자동 진입에 사용하지 않음 |
| 교차 심화신호 (`position_deepdive`) | Bitget×Toss×포지션 | 기록 중 | Toss outcome T+1·5·20; 표본 N<30 유보 |
| analyst briefing | 포지션 보고서 | 기록 중 | 종료 가격경로로 expected move 채점 |
| 페이퍼 진입/청산 (`paper_trade_entry`) | paper engine | 기록 중 | 청산 net PnL 및 R 결과 |
| FOMO 진입 판정 (`fomo_entry`) | WO-FCE-90 entry snapshot | 기록 중 | 청산 시 FOMO 귀속 손실 여부 |
| 알림 전달 (`alert_fired`) | alert store | 채점 불가 | 전달 envelope; 의미 판단은 별도 semantic row |
| 진입 스냅샷 (`position_entry_snapshot`) | position deepdive | 채점 불가 | 당시 사실 기준점; 방향 주장이 아님 |
| intent 등록 (`entry_intent_registered`) | scout | 채점 불가 | 수명주기 감사; 발화 intent를 별도 채점 |
| 자율 강등·격리·승격 제안 | `autonomy_logs` | 채점 불가 | 거버넌스 결정 이력; 효과는 주간 ImprovementProof가 별도 평가 |

와이코프·유동성 sweep·하모닉 PRZ는 기존 `wyckoff_event`, `liquidity_sweep`, `harmonic_prz`로 원장과 종료 scorecard에 이미 연결돼 있다. 신규 감지기 추가 모라토리엄은 `AGENTS.md`를 따른다.

## 운영 표면

- 엔진 상태: 최근 7일 전체/기록/채점 대기/채점 불가와 미분류 유형을 표시한다.
- 주간 개선 리포트: `judgment_coverage_line` 한 줄을 함께 저장한다.
- 코드 registry: `backend/app/review/coverage.py`. 문서와 registry 불일치는 누락으로 취급한다.

## 정직성·회귀 규칙

- 결과가 없는 판단을 `correct`로 채우지 않는다.
- `scout_judgment_*`와 generic ledger 사이에 새 중복 테이블을 만들지 않는다.
- T+1·5·20이 도래하지 않은 행은 pending으로 남긴다.
- 원장 행은 보존 정책에서 삭제하지 않는다.
