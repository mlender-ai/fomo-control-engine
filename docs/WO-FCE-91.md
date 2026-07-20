# WO-FCE-91 — Dogs 철거: 레거시 라우트 정본 통합

우선순위: P1 (숨겨진 중복 route와 복사본이 제품 정본을 흐림)

## 착수 전 확인

- [x] 같은 WO/route consolidation 브랜치 없음
- [x] 최근 로그·워킹트리에서 기존 철거 작업 없음
- [x] `dashboard/app` 전체 page와 component import 전수 확인

## 작업

- 정본·흡수·즉시 제거 판정표를 `RouteConsolidation.md`에 고정했다.
- `_archive` 7개 page와 공개 레거시 page 8개를 제거했다.
- 사용처가 사라진 legacy shell 9개를 제거했다.
- route 자산 검사와 E2E 목록을 정본 7개 고정 route로 축소했다. 두 개의 동적
  deep link는 해당 상위 흐름 테스트에서 검증한다.
- 연구·검증·shadow 백엔드 API와 원장 데이터는 감사 자산으로 유지했다.

## 유실 감사

- validation/calibration 계기는 `/engine?tab=status`에 존재한다.
- shadow v2의 FOMO 귀속은 `/performance`에 존재한다.
- 포지션 상세는 `/`의 query 기반 cockpit에 존재한다.
- 거래 목록/상세 복기는 `/trades`에 존재한다.
- 심볼 점수 상세는 `/dashboard/[symbol]`에 존재한다.

## 완료 정의

- [x] route count 감소 및 `_archive` 제거
- [x] 판정표·유일 기능 대조 문서화
- [x] `npm run build` + `npm run check:local-assets`
- [x] HARNESS.md 전체 게이트
- [x] origin/main 반영 + CI success

## HANDOFF

- 목표: 화면 정본을 하나로 줄이고 감사 데이터는 보존한다.
- 한 일: 레거시 route/page/shell 제거, 정본 route QA 목록 갱신.
- 안 한 일/막힌 곳: 없음.
- 다음 액션: 정본 route에 신규 기능을 흡수할 때 route count·자산 검사를 함께 갱신.
- 검증: backend 570 tests·coverage 77.02%, frontend lint/typecheck/build, E2E 21/21, 정본 자산 28개 2xx, 레거시 7개 404.
- 머지: origin/main 반영 완료 · CI 29711584136 success.
