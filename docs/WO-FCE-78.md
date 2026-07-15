# WO-FCE-78 — 무료 실현 청산 히트맵

우선순위: P1 (유료 Coinglass 없이도 실제 청산 가격·시각을 관측할 수 있어 수급 판단의 빈칸을 줄인다)
선행: WO-FCE-20 파생 데이터 계층

## 착수 전 확인 (AGENTS.md 불변 규칙 2)
- [x] `git branch -a` 에 같은 WO 브랜치 없음
- [x] `git log --oneline -15` + `git status` 에 기존 구현 없음
- [x] `grep -r "WO-FCE-78" docs/ backend/ dashboard/` 결과 없음

## 진단 (코드 확정)
- `backend/app/marketdata/coinglass.py`는 유료 API 키가 있을 때만 예상 청산 가격 클러스터를 수집한다.
- `docs/Derivatives.md`는 기존 Coinglass 시간별 청산 합계에 가격 필드가 없어 히트맵을 역산하지 않는다고 명시한다.
- Bitget은 2026-06-25 공개 `GET /api/v3/market/liquidations`를 추가했고 최근 3일의 실제 청산 주문에 `price`, `amount`, `side`, `ts`를 제공한다. 이 데이터는 API 키가 필요 없다.
- 실현 청산 이력은 Coinglass의 미래 예상 청산 분포 모델과 다른 관측 자료다. 둘을 같은 이름이나 의미로 표시하면 안 된다.

## 작업
### 1. Bitget 공개 청산 수집
- Bitget provider에서 최근 청산 이력을 페이지 단위로 읽고, 안정 ID로 `liquidation_events`에 중복 없이 저장한다.
- 가격과 방향은 실측으로, `price × amount` 명목액은 REST 문서에 amount 단위가 명시되지 않은 추정치로 저장한다.

### 2. 실현 청산 히트맵 집계 API
- 시간·가격 버킷별 실현 청산 강도를 결정론적으로 집계한다.
- 조회와 수동 갱신 API를 분리하고 24시간/72시간 범위를 지원한다.
- 응답에 표본 N, 관측 범위, 데이터 한계, 미래 예측 아님을 포함한다.

### 3. 공용 분석 화면
- 포지션·스카우트가 공유하는 분석 화면에 실제 청산 히트맵을 표시한다.
- 롱/숏 청산 합계, 상위 가격대, 데이터 시각, 무료 공개 소스를 함께 표시한다.
- 데이터가 없거나 Bitget provider가 아니면 원인을 숨기지 않는다.

## 수용 기준
- [x] 공개 Bitget 응답이 가격별 `LiquidationEvent`로 파싱되고 재수집 시 중복되지 않는다.
- [x] 24h/72h 시간·가격 셀과 상위 가격대를 API가 반환한다.
- [x] 스카우트와 포지션 공용 화면에서 실제 데이터 기반 히트맵을 볼 수 있다.
- [x] 화면에 `실현 청산`, `미래 예상 청산대가 아님`, 표본 N이 명시된다.
- [x] mock/live 테스트, 프론트 lint/typecheck/build가 통과한다.

## 금지
- 실현 청산 이력을 미래 예상 청산 맵으로 오인시키지 않는다.
- 추정 명목액을 거래소 확정 USD 금액으로 표현하지 않는다.
- 이 관측을 Entry Score, 방향 판정, 자동 진입에 사용하지 않는다.
- 실주문·자동 승격·미확정 캔들 채점 경로를 추가하지 않는다.

## 문서
- 갱신할 `docs/*.md`: `Derivatives.md`, `Worker.md`, `LiquidationIntelligence.md`

## 완료 정의 (공통)
- [x] HARNESS.md 게이트 통과
- [x] docs 갱신
- [x] origin/main 반영 + CI success 확인 (불변 규칙 1·3)
