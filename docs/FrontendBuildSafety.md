# Frontend Build Safety

> 상태: 강제 규칙. `AGENTS.md` 불변 규칙 4와 `HARNESS.md` Gate 2가 이 문서를 집행한다.

## 목적

실행 중인 Next.js 프로덕션 서버와 빌드 산출물의 버전 불일치로 전체 화면의 CSS와
JavaScript가 깨지는 사고를 방지한다. 차트, 포지션, 스카우트 등 특정 화면 문제가
아니라 모든 라우트가 동시에 무스타일로 보이면 먼저 자산 정합성을 확인한다.

## 2026-07-17 사고 기록

- 이전 `next start` 프로세스가 8876 포트에서 구형 HTML을 메모리에 유지했다.
- 같은 `.next` 디렉터리에 새 `next build`가 실행돼 기존 CSS/JS 청크가 교체됐다.
- 구형 HTML이 참조한 CSS 파일 두 개가 더 이상 존재하지 않아 HTTP 500을 반환했다.
- 결과적으로 헤더·내비게이션·차트·카드·SVG 크기 규칙이 전 화면에서 사라졌다.
- 원인은 차트 렌더러나 개별 CSS 선택자가 아니라 **서버와 빌드 산출물의 세대 불일치**였다.

## 불변 조건

1. `.next`를 서비스하는 프로세스가 살아 있는 동안 `.next`를 다시 빌드하지 않는다.
2. 로컬 프로덕션 빌드는 `npm run build`만 사용한다. `next build` 직접 실행으로
   `prebuild` 가드를 우회하지 않는다.
3. Playwright는 `.next-e2e`만 사용한다. 로컬 프로덕션 `.next`와 공유하지 않는다.
4. 재기동 후 `npm run check:local-assets`가 성공하기 전에는 복구 완료로 보고하지 않는다.
5. 자산 검사 실패를 CSS 수정으로 덮지 않는다. 서버를 종료하고 깨끗한 빌드로 교체한다.

## 강제 장치

| 장치 | 역할 | 실패 의미 |
| --- | --- | --- |
| `prebuild` | 8876 서버가 살아 있으면 기본 `.next` 빌드 차단 | 기존 서버를 먼저 종료해야 함 |
| `FCE_NEXT_DIST_DIR=.next-e2e` | E2E 산출물을 프로덕션과 분리 | 테스트가 로컬 화면을 덮어쓰면 안 됨 |
| `build:e2e` | Next가 자동 변경한 타입 설정을 빌드 후 원복 | E2E 후 워킹트리가 흔들리면 안 됨 |
| `check:local-assets` | 정본 고정 route 7개의 CSS/JS 청크 전수 2xx 확인 | 서버·HTML·청크 중 하나가 불일치 |
| Playwright 전 라우트 회귀 | 스타일시트 수, 가로 넘침, 거대 SVG/버튼 검사 | 화면 구조 또는 CSS 로딩 회귀 |

## 정상 갱신 절차

```bash
cd dashboard

# 1. 실행 중인 npm run start:local 프로세스를 Ctrl-C로 종료
# 2. 프로덕션 빌드
npm run build

# 3. 새 빌드 시작
npm run start:local

# 4. 다른 터미널에서 전체 라우트 자산 확인
npm run check:local-assets

# 5. 화면 구조 변경이면 E2E까지 확인
npm run test:e2e
```

완료 조건:

- `check:local-assets`가 정본 고정 route 7개와 모든 발견 자산의 2xx를 보고한다.
- 프론트 변경이면 lint, typecheck, build가 통과한다.
- 화면·플로우 변경이면 E2E가 통과한다.
- `git status --short`가 비어 있고 main CI가 성공한다.

## 금지 절차

```bash
# 금지: 8876의 next start가 살아 있는 상태
npm run build

# 금지: prebuild 우회
npx next build

# 금지: E2E를 기본 .next에 빌드
FCE_NEXT_DIST_DIR=.next npm run build:e2e
```

빌드 가드를 끄는 환경변수나 예외 플래그를 추가하지 않는다. 다른 포트를 쓰더라도 같은
산출물 디렉터리를 서비스 중이면 동일한 금지 규칙이 적용된다.

## 장애 복구

가장 빠른 로컬 폴백은 `cd dashboard && npm run recover:local`이다. 이 명령은 아래
수동 절차와 동일한 순서를 강제하며, 8876이 다른 프로세스 소유면 안전하게 중단한다.

1. 실행 중인 프론트 서버를 종료한다.
2. 8876에 남은 `next start` 프로세스가 없는지 확인한다.
3. `npm run build`로 완전한 프로덕션 산출물을 만든다.
4. `npm run start:local`로 재기동한다.
5. `npm run check:local-assets`를 실행한다.
6. 기존 브라우저 탭은 이전 청크 주소를 보유할 수 있으므로 강력 새로고침한다.
7. 여전히 실패하면 HTTP 상태가 200이 아닌 자산 경로를 기록하고 배포를 중단한다.

부분 CSS 복구, 임의 청크 복사, 오래된 `.next`와 새 `.next` 혼합은 금지한다.

## 회귀 수용 기준

- 정본 고정 route 7개가 HTTP 2xx를 반환하고 동적 deep link는 E2E 상위 흐름에서 검증된다.
- 각 라우트 HTML이 참조하는 CSS/JS 청크가 전부 2xx다.
- 데스크톱·모바일에서 가로 넘침이 없다.
- 누락된 CSS로 인해 버튼·SVG·캔버스가 화면 대부분을 덮지 않는다.
- E2E 실행 전후 `next-env.d.ts`, `tsconfig.json`, 로컬 `.next`가 오염되지 않는다.
