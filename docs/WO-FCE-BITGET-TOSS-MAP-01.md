# WO-FCE-BITGET-TOSS-MAP-01 — Bitget 주식 선물 ↔ Toss 기초자산 조인

우선순위: P1 (Bitget RWA 선물의 실행 가격과 Toss 기초자산 구조 데이터를 같은 인스트루먼트로 안전하게 연결)
선행: WO-FCE-TOSS-SCOUT-01

## 착수 전 확인 (AGENTS.md 불변 규칙 2)
- [x] `git branch -a` 에 같은 WO 브랜치 없음
- [x] `git log --oneline -15` + `git status` 에 기존 구현 없음
- [x] `grep -r "WO-FCE-BITGET-TOSS-MAP-01" docs/ backend/ dashboard/` 결과 확인

## 진단 (코드 확정)
- 현재 Bitget 심볼 카탈로그는 RWA 여부와 자산 클래스는 보존하지만 기초자산 정식 명칭·상장 거래소를 제공하지 않는다.
- Toss 어댑터는 KR/US 주식 메타데이터와 캔들·가격을 읽을 수 있으나 Bitget 실행 인스트루먼트와의 승인된 조인 계층이 없다.
- 실제 오픈 포지션 MSTRUSDT·NBISUSDT·SOXLUSDT는 Bitget `isRwa=YES`이고 BTCUSDT·ETHUSDT 및 HYPEUSDT는 순수 크립토라 Toss 조회 대상에서 제외해야 한다.

## 작업
### 1. 검증된 매핑과 승인 게이트
- 신규 `instrument_map` 마이그레이션과 저장소 모델을 추가한다.
- 버전 관리되는 기초자산 신원(정식 영문명·거래소·자산유형)을 Toss 종목 메타데이터와 대조한다.
- 일치 후보도 `pending`으로만 만들고 사용자 승인 후에만 `verified`로 전환한다.

### 2. 읽기 전용 조인
- Bitget 현재가를 price of record로 유지한다.
- verified 매핑에 한해 Toss 일봉 구조를 현재 베이시스 비율로 정렬하고 원본 가격·기준시각을 함께 보존한다.
- 순수 크립토와 pending/rejected 매핑은 Toss 조인 호출을 하지 않는다.

### 3. 통합 관리 UI
- 실제 포지션·워치리스트를 단일 관리 표면에 표시한다.
- Bitget 전용/승인 대기/Bitget 실행+Toss 차트 상태와 승인·거부 동작을 제공한다.
- 조인 차트에 베이시스, Toss 세션 stale, 레버리지 ETF 경고와 소스별 가격을 노출한다.

## 수용 기준
- [x] 정식 명칭·거래소·자산유형 불일치 후보는 rejected
- [x] pending 매핑은 조인에 미사용
- [x] BTC·ETH 등 순수 크립토의 Toss 호출 0건
- [x] verified 종목은 Bitget 현재가와 Toss 구조 캔들·레벨을 한 차트에 표시
- [x] 베이시스와 원본 가격, stale 기준시각, 레버리지 ETF 경고 노출
- [x] 승인/거부 UI 동작
- [x] 기존 주문 경로 무변경

## 금지
- 티커 문자열만으로 verified 처리하지 않는다.
- Bitget 전체 선물 카탈로그를 Toss로 스캔하지 않는다.
- 순수 크립토에 Toss를 연결하지 않는다.
- Toss 주문·계좌 API를 추가하지 않는다.
- 매핑 승인 또는 분석 결과를 자동 진입·자동 승격 조건으로 사용하지 않는다.

## 문서
- 갱신할 `docs/*.md`: `docs/Scout.md`, 본 WO

## 완료 정의 (공통)
- [x] HARNESS.md 게이트 통과
- [x] docs 갱신
- [x] origin/main 반영 + CI success 확인 (불변 규칙 1·3)
