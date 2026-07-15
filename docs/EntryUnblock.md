# WO-FCE-77 — 페이퍼 진입 병목 자백과 안전 완화

우선순위: P0 (신규 진입 0이 지속되면 4주 비교 표본이 성립하지 않음)
선행: WO-FCE-71, WO-FCE-72, WO-FCE-74

## 착수 전 확인

- 기존 진입 평가는 `backend/app/paper/policy.py:evaluate_entry`의 다중 AND 게이트를 유지한다.
- 기존 candidate bootstrap은 `N>=15`, `win@1R>=50%`를 요구하며 자동 승격과 분리되어 있다.
- WO-77은 validated 승격 기준, 체크리스트 기준, 스탠스 로직을 변경하지 않는다.

## 진단

퍼널은 최초 탈락 관문과 숫자만 저장해 체크리스트의 어떤 항목이 실패했는지, 현재 방향 candidate가 어느 정도 표본을 보유했는지 설명하지 못했다. 또한 validated가 없는 초기 구간에서 기존 bootstrap 표본 하한까지 도달하지 못하면 안전장치가 정상이어도 신규 진입이 장기간 0일 수 있었다.

## 작업

### 1. 병목 계측

- `0019_entry_block_log.sql`에 확정 flip 후보의 실패 관문별 로그를 저장한다.
- 동일 캔들 재처리는 결정론적 ID로 중복 저장하지 않는다.
- 체크리스트 점수와 실패 항목, ATR 기반 실제 R:R, candidate별 N·win@1R을 상세 사유에 포함한다.
- 엔진 상태 퍼널은 단계별 탈락 사유 top3와 체크리스트 항목별 통과율을 표시한다.

### 2. 한시 bootstrap 완화

- 벤치마크 시작 후 14일 이내이고 validated 시그니처가 0개일 때만 보조 기준을 평가한다.
- 기존 `N>=15`, `win@1R>=50%`를 먼저 평가한다.
- 기존 기준을 못 넘긴 candidate에만 `N>=8`, `win@1R>=45%`를 적용한다.
- 14일 경과 또는 validated 1개 이상 생성 시 보조 기준은 자동 비활성화된다.
- 보조 기준에 의존한 거래는 `gate_mode=candidate_bootstrap_relaxed`와 `bootstrap_relaxed=true`를 거래·퍼널에 기록한다.

## 수용 기준

- [x] `entry_block_log`가 관문별 상세 사유를 중복 없이 저장한다.
- [x] 퍼널 단계별 top3와 체크리스트 항목별 통과율이 API와 엔진 상태 화면에 노출된다.
- [x] 첫 14일의 `N=8`, `win@1R=45%` candidate가 다른 모든 게이트 통과 시 페이퍼 진입한다.
- [x] 14일 경과 후 같은 candidate는 통과하지 않는다.
- [x] 완화 진입은 `bootstrap_relaxed`로 분리 가능하다.
- [x] 표본 수와 승률 하한 없는 통과 경로가 없다.

## 금지

- candidate를 validated로 자동 승격하지 않는다.
- 성적 조건 없는 페이퍼 진입을 허용하지 않는다.
- 체크리스트·R:R·무효화 위생·신선도 임계를 수동 완화하지 않는다.
- relaxed 진입을 일반 validated 성과와 구분 없이 집계하지 않는다.

## 검증

- `backend/tests/test_paper_policy.py`: 한시 완화, 자동 복원, 태깅, 병목 상세 및 체크리스트 통계
- `backend/tests/test_sqlite_repository.py`: 로그 영속성과 중복 방지
- `HARNESS.md`의 백엔드·프론트 게이트를 통과해야 완료한다.
