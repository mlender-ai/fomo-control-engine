# WO-FCE-89 — Judgment Ledger 커버리지 전수화

우선순위: P0 (원장에 기록되지 않는 판단은 시간 해자에 축적되지 않음)
선행: 없음

## 착수 전 확인 (AGENTS.md 불변 규칙 2)
- [x] `git branch -a` 에 같은 WO 브랜치 없음
- [x] `git log --oneline -15` + `git status` 에 기존 구현 없음
- [x] `grep -r "WO-FCE-89" docs/ backend/ dashboard/` 결과 기존 구현 없음

## 진단 (코드 확정)
- `judgment_ledger`와 `judgment_scores`는 존재하지만 전체 조회·유형 registry·최근 커버리지 표면이 없었다.
- stance flip은 알림 envelope로만 기록됐고, 포지션 status event는 `position_events`에만 남아 의미 단위 판단 원장 행이 없었다.
- `autonomy_logs`와 Toss T+N outcome의 역할 관계가 문서화되지 않았다.

## 작업
### 1. 인벤토리와 의미 단위 배선
- `docs/JudgmentCoverage.md`에 전 유형·채점 정의·채점 불가 사유를 고정한다.
- stance flip과 포지션 상태 전이를 기존 generic ledger에 의미 단위로 기록한다.

### 2. 커버리지 계기
- 최근 7일 전체·기록·pending·unscorable·미분류를 결정론 집계한다.
- 엔진 상태 카드와 주간 개선 리포트 한 줄에 같은 집계를 사용한다.

## 수용 기준
- [x] 판단 유형 인벤토리와 결과 horizon 문서화
- [x] stance flip·status transition 발생 시 원장 행 테스트
- [x] 엔진 상태에서 실데이터 커버리지 노출
- [x] 미분류 유형은 `attention`, 억지 outcome은 `unscorable` 처리

## 금지
- 새 원장 테이블 또는 같은 판단의 이중 기록 시스템 신설 금지
- 결과 미도래 판단을 correct로 채우기 금지
- 자동 승격·실주문 경로 변경 금지

## 문서
- `docs/JudgmentCoverage.md`
- `docs/Calibration.md`

## 완료 정의 (공통)
- [ ] HARNESS.md 게이트 통과
- [x] docs 갱신
- [ ] origin/main 반영 + CI success 확인 (불변 규칙 1·3)
