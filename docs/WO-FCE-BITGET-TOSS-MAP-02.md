# WO-FCE-BITGET-TOSS-MAP-02 — 라이브 포지션 기초자산 소스 가시화

우선순위: P1 (검증된 Toss 조인이 라이브 포지션 화면에서 보이지 않고 주식 선물에도 크립토 고래 패널이 노출되는 오인을 제거)
선행: WO-FCE-BITGET-TOSS-MAP-01

## 착수 전 확인 (AGENTS.md 불변 규칙 2)
- [x] `git branch -a` 에 같은 WO 브랜치 없음
- [x] `git log --oneline -15` + `git status` 에 기존 구현 없음
- [x] `grep -r "WO-FCE-BITGET-TOSS-MAP-02" docs/ backend/ dashboard/` 결과 확인

## 진단 (코드 확정)
- `dashboard/components/live-position-cockpit.tsx`는 선택 자산의 종류와 무관하게 `PositionWhaleBanner`를 렌더링하고, 포지션 카드에도 고래 요약을 붙인다.
- 매핑 승인 표면은 주식 스카우트 화면에만 있어 라이브 포지션의 `pending`/`verified` 상태와 Toss 조인 여부를 즉시 확인할 수 없다.
- 차트는 verified 매핑의 `underlying_join`을 이미 표시할 수 있으나, 화면 상단에 소스 역할과 승인 상태가 없어 사용자가 데이터 부재로 오인한다.

## 작업
### 1. 자산별 관측 패널 분기
- RWA 주식 선물에는 크립토 고래 추적 패널을 노출하지 않는다.
- Bitget 실행·실시간 가격과 Toss 기초자산 차트·구조 분석의 역할을 한 패널에서 표시한다.

### 2. 라이브 포지션 매핑 상태와 승인
- 현재 포지션·워치리스트의 매핑 상태를 라이브 포지션 화면에서 동기화한다.
- pending 후보는 검증 근거와 함께 수동 승인 버튼을 제공하고, 승인 직후 차트 조인을 다시 불러온다.

### 3. 포지션 카드 소스 라벨
- 주식 선물 카드에는 고래 요약 대신 `Toss 연결`, `승인 대기`, `매핑 거부` 상태를 표시한다.
- 순수 크립토에만 기존 고래 관측 요약을 유지한다.

## 수용 기준
- [x] NBISUSDT·SOXLUSDT 선택 시 상단에 Bitget/Toss 소스 역할과 조인 데이터가 표시됨
- [x] 주식 선물 선택 시 고래 추적 배너와 고래 카드 라벨이 표시되지 않음
- [x] pending 주식 선물을 라이브 포지션 화면에서 수동 승인하면 차트 조인이 즉시 갱신됨
- [x] BTCUSDT·ETHUSDT 등 순수 크립토는 Toss 호출/라벨 없이 기존 고래 관측 유지
- [x] 데스크톱·모바일에서 소스 패널이 잘리거나 가로 넘침이 없음

## 금지
- 매핑을 자동 승인하지 않는다.
- 티커 문자열만으로 verified 처리하지 않는다.
- 순수 크립토에 Toss를 연결하지 않는다.
- Toss 주문·계좌 API 또는 Bitget 실주문 경로를 추가하지 않는다.
- 관측 데이터를 자동 진입·자동 승격 근거로 사용하지 않는다.

## 문서
- 갱신할 `docs/*.md`: `docs/Scout.md`, 본 WO

## 완료 정의 (공통)
- [x] HARNESS.md 게이트 통과
- [x] docs 갱신
- [x] origin/main 반영 + CI success 확인 (불변 규칙 1·3)
