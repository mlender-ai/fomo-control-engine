# WO-FCE-79 — RWA 풋·콜 계약 관측

우선순위: P1 (SOXL 스카우트에서 현물·선물 흐름만으로는 미국 옵션 포지셔닝을 확인할 수 없음)
선행: WO-FCE-69

## 착수 전 확인 (AGENTS.md 불변 규칙 2)
- [x] `git branch -a` 에 같은 WO 브랜치 없음
- [x] `git log --oneline -15` + `git status` 에 기존 구현 없음
- [x] `grep -r "WO-FCE-79" docs/ backend/ dashboard/` 결과 없음

## 진단 (코드 확정)
- `backend/app/marketdata/coinglass.py`의 옵션 탐색은 유료 Coinglass 키가 있는 BTC·ETH에만 한정된다.
- `dashboard/components/position/CompactChartWorkspace.tsx`는 Coinglass 옵션 요약만 표시하므로 `SOXLUSDT` 같은 Bitget RWA 선물에는 풋·콜 계약 지표가 없다.
- `backend/app/notify/bot/formatters.py`의 스카우트 응답에는 옵션 관측 블록이 없다.
- OCC 공식 Series Search와 Volume Query는 API 키 없이 전일 결제 기준 OI와 일일 계약량을 제공한다.

## 작업
### 1. OCC 옵션 수집
- `backend/app/marketdata/occ_options.py`에서 공식 OCC 응답을 파싱하고, 종목별 캐시와 실패 격리를 적용한다.
- 정확한 기초자산 심볼만 집계해 조정계약 심볼을 섞지 않는다.

### 2. 공용 분석 응답 연결
- 주식·지수 RWA 분석에만 옵션 요약을 붙인다.
- 옵션 데이터는 관측 전용이며 TA, 종합 방향, 알림 점수에 사용하지 않는다.

### 3. 텔레그램·대시보드 표시
- `/scout SOXL`에 콜/풋 OI, P/C OI, 전일 계약량과 P/C 계약량을 표시한다.
- 포지션·스카우트 공용 Money Flow 카드에도 같은 요약을 표시한다.

## 수용 기준
- [x] 공식 OCC 무키 응답에서 SOXL 콜·풋 OI와 일일 계약량을 파싱한다.
- [x] `SOXLUSDT` 분석 응답에 출처·기준일·풋콜 비율이 포함된다.
- [x] 텔레그램과 대시보드가 풋·콜 계약을 구분해 표시한다.
- [x] OCC 장애가 차트·스카우트 분석 전체를 실패시키지 않는다.
- [x] 옵션 관측값이 방향 판정이나 자동 진입에 사용되지 않는다.

## 금지
- 옵션 수치를 검증 없이 상승·하락 신호나 컨플루언스 점수에 반영하지 않는다.
- 미확정 장중 수치를 전일 확정치로 표기하지 않는다.
- 실계좌 주문 경로를 만들지 않는다.

## 문서
- 갱신할 `docs/*.md`: `docs/MoneyFlow.md`, `docs/WO-FCE-79.md`

## 완료 정의 (공통)
- [x] HARNESS.md 게이트 통과
- [x] docs 갱신
- [ ] origin/main 반영 + CI success 확인 (불변 규칙 1·3)
