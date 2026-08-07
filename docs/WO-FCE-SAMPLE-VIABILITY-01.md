# WO-FCE-SAMPLE-VIABILITY-01 — 검증 표본 생성 능력 복구

우선순위: P0 — 관측일을 더 쌓아도 검증되지 않는 트랙이 있다는 사실이 드러났다.
선행: `WO-FCE-OBSERVATION-INTEGRITY-01` (완료)
절대 제약: **실거래 주문 실행 전면 금지 (C5 봉인 불변)**

## 진단 (코드 확정)

| 사실 | 근거 |
| --- | --- |
| 폴리 보유 9건 중 8건이 2027-01-01 만기, 검증 창 내 정산 예정 0건 | `app/poly_paper/store.py:31-38` (직전 WO Phase 4 실측 2026-08-05) |
| 선정 기준에 만기 **상한**이 없었다 | `app/poly_paper/service.py::_apply_market_gates` — `min_days_to_resolution` 만 존재 |
| 휴장 달력이 없어 공휴일이 유실일에 섞였다 | 직전 WO `docs/ObservationIntegrity.md` §"알려진 한계 — 공휴일" |
| KR 정규장(00:00~06:30 UTC)은 절전 구간(17:00~20:00 UTC)과 겹치지 않는다 | `app/worker/observation.py::LATE_SESSION_UTC` + `_SESSIONS["KR"]` — 구조적 |
| checklist 항목별 통과율은 있었으나 **유일 탈락 사유 건수**가 없었다 | `app/paper/service.py::_checklist_pass_rates` (변경 전) |
| "검증 완료"가 일수 기준이라 표본 0인 트랙도 진행 중으로 보였다 | `app/worker/observation.py::verification_clock` |

## 작업

### PHASE 1 — 표본 생성 능력 판정
- `app/validation/sample_viability.py` 신설. 트랙별 유효일당 진입(+95% CI)·청산 완료율·
  D+28 예상 표본·판정(`VIABLE`/`SLOW`/`STRUCTURALLY_BLOCKED`/`INSUFFICIENT_DATA`).
- 진입률의 분모는 **유효 관측일**. 유효일에 발생한 진입만 분자로 센다.
- `scripts/sample_viability_report.py` — 운영 DB에서 표를 실측으로 채우는 CLI.
- 산출물: [`docs/validation/SAMPLE_VIABILITY.md`](validation/SAMPLE_VIABILITY.md)

### PHASE 2 — 폴리 선정 기준 수정
- 진단 먼저: `PolyPaperStore.expiry_bias_diagnosis()` — 만기 구간별 유동성·총엣지·비용후엣지·
  통과율. 선정 점수에 만기 항은 없으므로, 편향이 있다면 이 간접 경로여야 한다.
- 수정: `params/poly-v3.json` 에 `max_days_to_resolution`·`settlement_buffer_days` 추가.
  `scoring_cutoff()` → `resolution_beyond_scoring_window` 로 신규 진입만 차단.
- 대조: 엔진 응답 `expiry_filter` 블록에 필터 적용 **전** 만기 분포와 걸러낸 건수.
- 기존 보유는 청산하지 않는다. 미실현과 실현은 합산하지 않는다.

### PHASE 3 — 주식 KR 커버리지 진단
- **휴장 오계상 여부를 먼저 확인**했다. `app/worker/market_calendar.py` 신설 —
  확정 휴장일만 분모에서 빼고, 미확정 후보는 분모를 바꾸지 않은 채 사람 확인용으로 올린다.
- `observation.gap_axes()` / `axis_breakdown()` — 휴장 / 호스트 절전 / 장 시간 경계 /
  소스 응답 4축 분해. 관측 0건인 날을 "경계 결함"으로 오귀인하지 않는다(종일 정지다).

### PHASE 4 — 크립토 checklist 병목 계측
- `_checklist_pass_rates` 에 `sole_block_count`(이 항목 하나만 실패해 탈락한 건수) 추가.
- `_checklist_bottleneck` — checklist 가 유일한 미통과 게이트였던 건수 + 최다 유일 탈락 항목.
- **임계값은 건드리지 않았다.** 계측만 한다.

### PHASE 5 — 호스트 지속성
- `scripts/local/measure-resources.sh` — VPS 사양을 추측이 아니라 실측으로 산정하기 위한 계측.
- 비교표: [`docs/validation/HOST_PERSISTENCE.md`](validation/HOST_PERSISTENCE.md). **선택하지 않았다.**

### PHASE 6 — 검증 완료 정의 재정립
- `validation_completion()` — 유효일 ≥ 28 AND 표본 ≥ 30 AND 국면 ≥ 2, 트랙별 독립 판정.
- 국면 라벨을 **진입 시점에** 기록(`entry_evidence.market_regime`). 사후 라벨링은 룩어헤드.
- `/validation` 화면 신설 + `GET /api/system/paper/diagnosis` 의 `sample_viability` 블록.
- 정의: [`docs/validation/COMPLETION_DEFINITION.md`](validation/COMPLETION_DEFINITION.md)

## 수용 기준

- [x] 4개 트랙 전부에 대해 채점 가능 표본 예상치와 판정이 산출된다 (`sample_viability_report`)
- [x] 폴리 신규 진입이 검증 창 내 만기 시장으로 이루어진다 + 수정 전후 만기 분포 대조 존재
- [x] KR 커버리지 저하 원인이 축별로 분해되고 **휴장 오계상 여부가 먼저 확인**된다
- [x] checklist 항목별 통과율과 **유일 탈락 사유 건수**가 대시보드에 노출된다
- [x] 호스트 지속성 비교표 + 각 안의 US 표본 회복 예상치
- [x] 검증 완료 정의가 3조건으로 바뀌고 대시보드가 이를 반영한다
- [x] **임계값·게이트 파라미터 diff 0** — `tests/test_poly_expiry_filter.py`, `tests/test_checklist_instrumentation.py` 가 고정
- [x] **주문 실행 봉인 불변** — 이 WO 는 주문 경로를 건드리지 않는다
- [x] 전 게이트 통과 (pytest 967 · ruff · mypy 170/174 · 커버리지 78.29%/코어 88.43% · next build)

## 금지 (이 WO 에서 하지 않은 것)

- 실거래 주문 — 전면 금지, 예외 없음
- checklist 임계값 완화 — 품질 게이트다. 계측만 했다
- 고래 승률의 선정 로직 편입 — 병행 관측 유지
- 신규 전략·신호 추가
- PHASE 5 의 안 **선택** — 비교표까지만 만들었다

## 사람 확인이 필요한 항목

1. ~~**2026-07-17 제헌절 공휴일 재지정 시행 여부.**~~ → **확인 완료**
   (WO-FCE-VALIDATION-VERDICT-01 Phase 5): 2026-04-28 국무회의 의결로 재지정 확정, KRX 전
   시장 휴장. `KR_CONFIRMED` 로 이동했다. KR 검증 시계는 첫 유효일(07-22) 이후만 세므로
   유효일·유실일 수치는 불변이고, 창 전체 커버리지 평균과 소급 표기만 바뀐다.
2. **호스트 지속성 안 선택** (A/B/C/D). `docs/validation/HOST_PERSISTENCE.md` §3.
3. **`settlement_buffer_days=2.0` 의 타당성.** 정산 지연 실측 표본이 적어 보수적으로 잡았다.
   `PolyPaperStore.settlement_latency()` 로 다시 재서 갱신한다.

## 문서

- 신설: `docs/validation/SAMPLE_VIABILITY.md`, `docs/validation/COMPLETION_DEFINITION.md`,
  `docs/validation/HOST_PERSISTENCE.md`
- 갱신: `docs/ObservationIntegrity.md`, `docs/PolymarketPaperTrading.md`, `docs/Validation.md`
