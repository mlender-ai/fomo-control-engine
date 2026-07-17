# WO-FCE-84 — 관제 읽기 경로 병목 제거

우선순위: P1 (외부 관측 지연이 핵심 엔진 화면 전체를 막고, 열린 거래 수만큼 외부 시세 호출이 누적됨)
선행: WO-FCE-77, WO-FCE-83

## 착수 전 확인 (AGENTS.md 불변 규칙 2)

- [x] `git branch -a` 에 같은 WO 브랜치 없음
- [x] `git log --oneline -15` + `git status` 에 기존 구현 없음
- [x] `grep -r "WO-FCE-84" docs/ backend/ dashboard/` 결과 없음

## 진단 (코드 확정)

- `backend/app/services/runtime.py`의 `paper_dashboard()`는 도메인 서비스가 저장된 `paper_engine_state.last_price`로 이미 만든 `exit_monitor`를 받은 뒤에도 열린 거래마다 `market_provider.get_snapshot()`을 순차 재호출했다. 읽기 1회당 외부 전체 스냅샷 호출 수가 `open_trades` N과 같았다.
- 실제 저장소 프로파일에서 응답 내부 시간의 92%(`0.244/0.265초`)가 활성 상태 계산에 집중됐다. 같은 요청에서 페이퍼 유니버스를 두 번 만들고, 최신순 인덱스 없이 최근 발굴 500건 전체를 역직렬화한 뒤 통과 0건을 버리고 있었다.
- `dashboard/components/engine-trading-shell.tsx`는 페이퍼 대시보드와 선택 관측인 고래 데이터를 하나의 `Promise.all`로 묶었다. 고래 요청 하나가 실패하면 정상인 페이퍼 응답도 버려져 핵심 대결·포지션·상태 화면을 사용할 수 없었다.

## 작업

### 1. 페이퍼 읽기 경로를 저장 상태 투영으로 고정

- `services/runtime.paper_dashboard()`는 `paper.service.paper_dashboard()` 결과를 그대로 반환한다.
- 현재가 기반 `exit_monitor`는 워커가 저장한 최신 `last_price`를 사용하는 기존 `_open_trade_payload()` 한 경로만 유지한다.
- 외부 시세 새로 수집은 워커/명시적 실행 책임으로 남긴다.
- 활성 상태 계산은 페이퍼 유니버스를 요청당 한 번만 만들고 다음 확정 캔들 계산에 재사용한다.
- 최근 발굴 500건이라는 기존 의미는 유지하되, SQLite가 그 창 안의 `gate_passed` 행만 반환해 불필요한 JSON/Pydantic 역직렬화를 제거한다.
- 새 `0020` 마이그레이션으로 `universe_discoveries(created_at DESC)` 읽기 인덱스를 추가한다. 기존 적용 마이그레이션은 수정하지 않는다.

### 2. 선택 관측과 핵심 화면 로딩 분리

- 페이퍼 대시보드 응답을 기다리는 동안 고래 데이터는 별도 요청으로 갱신한다.
- 고래 요청 실패는 경고로 격리하고 페이퍼 대결·포지션·상태 화면은 정상 렌더한다.
- 고래 탭의 30초 갱신과 수동 갱신은 같은 격리된 로더를 재사용한다.

## 수용 기준

- [x] `GET /api/paper/dashboard` 서비스 경로는 `market_provider.get_snapshot()`을 호출하지 않는다.
- [x] 열린 거래의 `exit_monitor` 계약은 저장된 워커 가격 투영으로 유지된다.
- [x] 최근 발굴 창의 통과 행 필터는 `LIMIT` 이후 적용되어 기존 500건 창 의미를 유지한다.
- [x] `/api/onchain/whales`가 실패해도 `/engine`의 대결 화면이 렌더되고 격리 경고가 표시된다.
- [x] 외부 호출 수는 읽기당 `N`회에서 `0`회로 감소한다.

## 금지

- 페이퍼 진입·청산·시그니처 검증·후보 승격 정책 변경 금지
- 미확정 캔들 사용, 자동 승격, 실계좌 주문 경로 추가 금지
- 고래 관측을 방향 판정이나 자동 진입 근거로 승격 금지

## 문서

- 갱신: `docs/Architecture.md`
- 신규: `docs/WO-FCE-84.md`

## 완료 정의 (공통)

- [x] HARNESS.md 게이트 통과
- [x] docs 갱신
- [x] origin/main 반영 + CI success 확인 (불변 규칙 1·3)

## 검증 기록

- 백엔드: ruff check/format, import cycle, mypy baseline `165/174`, pytest `501 passed, 2 deselected`, 총 커버리지 `76.99%`, 코어 `88.07%` 통과
- 실제 데이터셋 함수 프로파일: `paper_dashboard()` `265ms → 18ms`(약 93% 감소), `_paper_activation()` `244ms → 5ms`
- 재시작 후 실제 HTTP warm 응답: `16.4ms`, `14.6ms` (각 200)
- 프론트: ESLint error 0, TypeScript, Next production build 통과
- E2E/시각 회귀: `17 passed`
- 로컬 제품 자산: 14개 라우트와 29개 CSS/JS 자산 모두 2xx
