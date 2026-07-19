# WO-FCE-LOCAL-RECOVERY-01 — 로컬 프론트 긴급 폴백

우선순위: P0 — `8876` 중단 시 전 제품 화면에 접근할 수 없다.
선행: `FrontendBuildSafety.md`

## 착수 전 확인 (AGENTS.md 불변 규칙 2)

- [x] `git branch -a` 에 같은 WO 브랜치 없음
- [x] `git log --oneline -15` + `git status` 에 기존 구현 없음
- [x] `rg "LOCAL-RECOVERY|recover:local" docs/ backend/ dashboard/` 결과 확인

## 진단 (코드 확정)

- 프론트가 죽으면 `ERR_CONNECTION_REFUSED`만 노출되고, 안전 빌드·재기동·자산 검사를
  한 번에 수행하는 복구 명령이 없었다.
- 기존 `prebuild`와 `check:local-assets`는 사고 예방·검사 게이트였지만 서버 종료와 재시작은
  수동 절차였다.

## 작업

### 1. 실패-폐쇄 복구 명령

- `dashboard/scripts/recover-local.mjs`에서 8876 리스너 소유권 확인, 종료, 빌드,
  재기동, 전체 자산 검사를 순서대로 수행한다.
- FCE Next가 아닌 프로세스가 포트를 쓰거나 자산 검사가 실패하면 복구 완료로 처리하지 않는다.

### 2. 운영 문서

- `docs/LocalRecovery.md`에 한 줄 복구 명령과 백엔드 별도 기동 경로를 기록한다.

## 수용 기준

- [x] `npm run recover:local` 성공
- [x] 제품 14개 라우트와 발견된 29개 CSS/JS 자산 2xx
- [x] `npm run lint` error 0
- [x] `npm run typecheck` 통과

## 금지

- FCE Next가 아닌 8876 리스너 강제 종료 금지
- 실행 중 `.next` 덮어쓰기 금지
- 실주문 경로 변경 금지

## 문서

- `docs/LocalRecovery.md`
- `docs/FrontendBuildSafety.md`

## 완료 정의 (공통)

- [x] HARNESS.md 프론트 게이트 통과
- [x] docs 갱신
- [ ] origin/main 반영 + CI success 확인
