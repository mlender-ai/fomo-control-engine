# WO-FCE-POSITION-DATA-INTEGRITY-01 — 포지션 가격·점수 정합성

우선순위: P0 (같은 포지션의 가격·점수가 서로 다르게 보이면 관제 판단을 신뢰할 수 없음)
선행: WO-FCE-LIVE-UI-HIERARCHY-01

## 착수 전 확인 (AGENTS.md 불변 규칙 2)
- [x] `git branch -a` 에 같은 WO 브랜치 없음
- [x] `git log --oneline -15` + `git status` 에 기존 구현 없음
- [x] `grep -r "WO-FCE-POSITION-DATA-INTEGRITY-01" docs/ backend/ dashboard/` 결과 없음

## 진단 (코드 확정)
- `backend/app/positions/action_plan.py`의 `_format_price()`가 가격 크기와 무관하게 최대 8자리 소수까지 문장에 넣어 ETH 레벨 `1674.31008598`을 그대로 노출한다. 계산 원본은 유효하지만 사용자 표시 정밀도는 부적절하다.
- `backend/app/services/http_handlers.py`의 compact 응답은 저장 스냅샷의 상위 2개 critical level로 액션 플랜을 재구성하고, detail 응답은 전체 차트 레벨로 다시 구성한다. 같은 ETH 포지션에서 compact headline은 `1551.31800896`, detail headline은 더 가까운 `1674.31008598`을 선택했다.
- 실데이터 역산 결과 ETHUSDT Health 25는 오류가 아니다. 원점수는 `100×0.30 + 0×0.20 + 100×0.20 + 69×0.20 + 73×0.10 = 71.1`이나 ROE `-83.92%`로 `pnl_state=0`이어서 문서화된 cap 25가 적용된다. Risk 28은 청산 거리 44.42%가 안전한 별도 축이다. 화면이 Health와 Risk를 구분하지 않아 오해 가능성이 있다.
- compact 분석에서 `reason_codes`가 누락돼 타입 계약(`string[]`)과 실제 응답(`null`)이 달랐다.

## 작업
### 1. 가격 표시 정밀도 정상화
- 원시 숫자는 계산용으로 보존하고, 액션 문장만 가격 크기별 표시 정밀도로 변환한다.
- 프론트는 구조화된 플랜으로 headline을 재생성해 과거 raw 문자열도 안전하게 표시한다.

### 2. 액션 플랜 단일 정본
- 분석 시 만든 전체 액션 플랜을 PositionSnapshot 분석 JSON에 함께 저장한다.
- compact 폴링은 저장된 정본을 사용하고, 과거 스냅샷에만 기존 재구성 폴백을 적용한다.

### 3. Health Score 감사 정보
- 가중 원점수, cap 사유·값, 최종 점수, 산식 버전, 정합 여부를 `health_integrity`로 기록한다.
- 화면의 25를 명시적으로 `건강도`로 표기하고 상세 근거를 툴팁으로 제공한다.
- compact 응답에도 `reason_codes`와 health 감사 정보를 유지한다.

## 수용 기준
- [x] ETH 원시 레벨 `1674.31008598`은 계산 JSON에 보존되고 문장에는 `1,674.31`로 표시된다.
- [x] 같은 저장 스냅샷의 analyze 응답과 compact 응답 액션 플랜이 동일하다.
- [x] Health 25의 원점수 71, cap 25, 최종 25가 API에서 검증 가능하다.
- [x] compact `reason_codes`가 배열로 유지된다.
- [x] 관련 백엔드·프론트 테스트와 HARNESS 게이트가 통과한다.

## 금지
- 원시 분석 가격을 반올림해 저장하지 않는다.
- Health Score와 Risk Score를 하나의 점수처럼 합치지 않는다.
- 자동 승격·미확정 캔들 채점·실주문 경로를 추가하지 않는다.

## 문서
- 갱신할 `docs/*.md`: `docs/HealthScore.md`, 본 WO

## 완료 정의 (공통)
- [x] HARNESS.md 게이트 통과
- [x] docs 갱신
- [x] origin/main 반영 + CI success 확인 (불변 규칙 1·3)
