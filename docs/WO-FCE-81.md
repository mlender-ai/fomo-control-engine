# WO-FCE-81 — OCC 맥스페인·만기 표시

우선순위: P1 (풋·콜 총계만으로는 어느 만기에 OI가 집중됐는지와 옵션 결제 압력 가격을 함께 판단하기 어렵다)
선행: WO-FCE-80

## 착수 전 확인 (AGENTS.md 불변 규칙 2)
- [x] `git branch -a` 에 같은 WO 브랜치 없음
- [x] `git log --oneline -15` + `git status` 에 기존 구현 없음
- [x] `grep -r "WO-FCE-81" docs/ backend/ dashboard/` 결과 없음

## 진단 (코드 확정)
- `backend/app/marketdata/occ_options.py`는 OCC series-search에서 만기·행사가·콜/풋 OI를 이미 파싱하지만 전체 합계와 상위 계약만 응답한다.
- `backend/app/notify/bot/formatters.py`와 `dashboard/components/position/CompactChartWorkspace.tsx`는 풋·콜 비율을 표시하지만 만기별 맥스페인 값은 표시하지 않는다.
- 따라서 별도 유료 API 없이 OCC 공식 공개 OI로 최근접 만기의 만기 손익 최소 행사가를 계산할 수 있다.

## 작업
### 1. 최근접 만기 맥스페인 계산
- 미래 계약 중 가장 가까운 만기를 선택한다.
- 해당 만기의 각 행사가를 결제가격 후보로 두고 콜·풋 매수자 총 내재가치가 최소인 가격을 계산한다.
- 동률이면 OI 가중 중심에 가까운 행사가, 그다음 낮은 행사가를 선택해 결과를 결정론적으로 만든다.

### 2. API·대시보드·텔레그램 표시
- OCC 옵션 응답에 `max_pain_price`, `max_pain_expiry`, `days_to_expiry`와 계산 기준을 추가한다.
- 풋·콜 카드와 `/scout` 옵션 블록에 맥스페인 가격, 만기일, D-day를 표시한다.

## 수용 기준
- [x] OCC 샘플에서 최근접 만기의 맥스페인 가격과 만기일이 결정론적으로 계산된다.
- [x] 대시보드 풋·콜 카드에서 맥스페인 가격·만기·D-day가 보인다.
- [x] 텔레그램 `/scout` 응답에서 동일 값이 보인다.
- [x] 기존 풋·콜·판정 테스트와 전체 게이트가 통과한다.

## 금지
- 맥스페인을 가격 목표나 방향 신호로 단정하지 않는다.
- 맥스페인·풋콜 데이터를 종합 판정, 자동 진입, 실주문에 반영하지 않는다.
- 미확정·미래 가격 데이터를 사용하지 않는다.

## 문서
- 갱신할 `docs/*.md`: `docs/MoneyFlow.md`, `docs/WO-FCE-81.md`

## 완료 정의 (공통)
- [x] HARNESS.md 게이트 통과
- [x] docs 갱신
- [x] origin/main 반영 + CI success 확인 (불변 규칙 1·3)
