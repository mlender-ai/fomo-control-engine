# WO-FCE-90 — Shadow Account v2: FOMO 정량 귀속

우선순위: P0 (제품명과 달리 기존 FOMO 귀속이 낮은 Entry Score+손실 사후 프록시뿐이었음)
선행: WO-FCE-89

## 착수 전 확인 (AGENTS.md 불변 규칙 2)
- [x] 같은 WO/주제 브랜치 없음
- [x] 최근 로그·워킹트리에서 기존 v2 구현 없음
- [x] `shadow/engine.py`의 구 프록시 위치 확인

## 진단 (코드 확정)
- `Trade`에 계획가·진입 간격·스카우트 경유·stance 정합 필드가 없어 진입 행동을 사후 재구성할 수밖에 없었다.
- 구 shadow는 `(entry_score < 65) and loss`를 FOMO로 간주해 원인과 결과를 뒤섞었다.

## 작업
### 1. 진입 순간 스냅샷
- 6개 필드와 결정론 FOMO Index/성분 분해를 Position에 저장하고 종료 Trade로 복사한다.
- 신규 migration으로 조회 열을 추가하되 기존 거래 payload는 null을 유지한다.

### 2. 귀속·원장·화면
- 월간 complete 표본만 FOMO 손실을 분리하고 GA에 표본 가드와 함께 표시한다.
- `fomo_entry` 판단을 기존 원장에 기록하고 종료 시 채점한다.
- shadow 구 프록시는 연속성 감사 값만 남기고 신규 귀속에서 제거한다.

## 수용 기준
- [x] 신규 진입에서 6필드·성분별 기여 캡처 테스트
- [x] legacy 거래 null/귀속 제외 및 표본 가드 테스트
- [x] 종료 시 fomo_entry 채점 테스트
- [x] GA 월간 FOMO 비용·최근 Index 분해 표면

## 금지
- 과거 거래 소급 추정 금지
- FOMO Index의 Entry Score·방향·자동 진입 주입 금지
- 투자 지시 또는 인과 단정 금지

## 문서
- `docs/ShadowAccount.md`
- `docs/JudgmentCoverage.md`

## 완료 정의 (공통)
- [x] HARNESS.md 게이트 통과
- [x] docs 갱신
- [ ] origin/main 반영 + CI success 확인 (불변 규칙 1·3)

## HANDOFF

- 목표: 진입 당시 행동을 스냅샷으로 고정해 FOMO 비용을 사후 추정 없이 귀속한다.
- 한 일: 6필드 FOMO Index, Position→Trade 복사, 원장 채점, GA 월간 비용과 표본 가드.
- 안 한 일/막힌 곳: 과거 거래는 소급 추정하지 않아 귀속 표본에서 제외.
- 다음 액션: 신규 complete 표본이 10건 이상 누적된 뒤 귀속 분포를 해석한다.
- 검증: HARNESS 백엔드·프론트·E2E 통과.
- 머지: origin/main 반영 대기.
