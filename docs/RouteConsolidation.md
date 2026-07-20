# FCE Route Consolidation

WO-FCE-91의 라우트 정본과 유실 감사를 기록한다. Git 이력이 아카이브이므로
실행 코드 아래에 별도 `_archive` 복사본을 두지 않는다.

## 판정 기준

- **정본 유지**: 다른 화면이 제공하지 않는 데이터나 작업 흐름이 있다.
- **흡수 후 제거**: 데이터와 작업 흐름이 이미 정본 화면에 있다.
- **즉시 제거**: 안내 스텁·리다이렉트·실행되지 않는 복사본뿐이다.

## 판정표

| 기존 route | 판정 | 정본/흡수 위치 | 유일 기능 감사 |
|---|---|---|---|
| `/` | 정본 유지 | 라이브 포지션 관제 및 `?position=` 상세 | 포지션 동기화·차트·계기판·메모·복기 유지 |
| `/dashboard/[symbol]` | 정본 유지 | 심볼 점수 상세 | 스냅샷 JSON·데이터 품질·점수 분해의 유일 deep link |
| `/scout` | 정본 유지 | 주식/크립토 후보 탐색 | market 목록보다 넓은 후보·Toss 조인 유지 |
| `/engine` | 정본 유지/흡수 | 페이퍼·원장·검증·캘리브레이션 | `/validation`, `/calibration`의 운영 계기 흡수 |
| `/review` | 정본 유지 | 복기 허브 | 거래·엔진·성적표 진입점 유지 |
| `/trades`, `/trades/[tradeId]` | 정본 유지/흡수 | 거래 목록·개별 복기 | `/journal` 기능 흡수 |
| `/performance` | 정본 유지/흡수 | 계좌 성적·월간 FOMO 귀속 | `/shadow`의 행동 귀속을 WO-FCE-90 v2로 흡수 |
| `/settings` | 정본 유지 | 연결·read-only 상태 | 설정 고유 기능 유지 |
| `/positions`, `/positions/[positionId]` | 흡수 후 제거 | `/` + `?position=` | 별도 shell은 동일 cockpit export였음 |
| `/journal` | 즉시 제거 | `/trades` | 이동 안내 스텁뿐, 데이터 없음 |
| `/markets` | 즉시 제거 | `/scout`, `/dashboard/[symbol]` | 안내 스텁뿐; 시장 API는 유지 |
| `/research` | 즉시 제거 | 감사 API 유지 | 안내 스텁뿐; 연구 run은 자동화/감사용 API로 유지 |
| `/shadow` | 흡수 후 제거 | `/performance` | FOMO v2 카드·분해·표본 가드 유지 |
| `/validation` | 흡수 후 제거 | `/engine?tab=status` | 실이력·원장·후보 검증 계기 유지 |
| `/calibration` | 흡수 후 제거 | `/engine?tab=status` | scorecard·자율 피드·검증 컨텍스트 유지 |
| `/_archive/**` | 즉시 제거 | Git history | 7개 page 복사본, 실행 정본 아님 |

## 결과

- Next page 파일: 24개 중 비제품 icon/manifest route를 제외한 page route 24→9.
- 제거: 공개 레거시 page 8개 + `_archive` page 7개 = 15개.
- 정본: `/`, `/dashboard/[symbol]`, `/scout`, `/engine`, `/review`,
  `/trades`, `/trades/[tradeId]`, `/performance`, `/settings` = 9개.
- 백엔드 연구·검증·shadow API와 저장 데이터는 삭제하지 않았다. 화면 중복 제거와
  감사 데이터 삭제를 혼동하지 않는다.

## 회귀 게이트

`dashboard/scripts/check-local-assets.mjs`와 E2E route audit는 위 정본 route만 검사한다.
프로덕션 `.next` 갱신은 `docs/FrontendBuildSafety.md`의 stop → `npm run build` →
`npm run start:local` → `npm run check:local-assets` 순서만 사용한다.
