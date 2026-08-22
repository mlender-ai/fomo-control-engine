# 방향 판정 재판정 하네스 (Replay Harness)

> WO-FCE-DIRECTIONAL-INTEGRITY-01 **Phase 0** 정본. 계측 전용 — 판정 로직 변경 0건.
> 정본 코드: `backend/app/validation/directional_replay.py`
> 실행 도구: `backend/scripts/directional_baseline_report.py`

## 0. 왜 필요한가 — 거래 결과로는 측정할 수 없다

이 WO 는 Phase 5개를 순차 적용한다. 각 Phase 의 효과를 무엇으로 측정하는가?

`SAMPLE-RATE-01` 의 결론은 **필요 표본이 트랙당 283건**이라는 것이었고, 현 검증 창은 4일
남았다. 즉 **PnL·승률로는 Phase 하나도 측정할 수 없다.** 남은 측정 축은 하나다 —
**판정 분포의 변화.**

> 이 하네스가 없으면 나머지 Phase 는 "고쳤다고 주장하는 변경"이 된다.

## 1. 무엇을 하는가

저장된 과거 캔들에 대해 **현재 엔진을 그대로 재실행**하고, 각 시점 판정의 **내부**를 기록한다.

| 축 | 필드 | 어느 결함의 대조축인가 |
|---|---|---|
| 와이코프 국면 | `wyckoff_phase` · `wyckoff_side` · `wyckoff_event_types` | D4·D8 |
| 선행 추세 | `trend_direction` · `prior_trend_conflict` | **D1** |
| 엔진별 stance | `engine_stances` · `judged_engines` | 증상 B |
| 커버리지 | `judged_engine_count` · `silent_weight_pct` | **E1** |
| 근거 수 vs 엔진 수 | `directional_evidence_count` · `directional_engine_count` | **E2** |
| 두 시계 | `raw_stance` (순간) · `stance` (채택) | **E3** |
| 점수·격차 | `long_score` · `short_score` · `margin_ratio` | 전 Phase 공통 |

## 2. 룩어헤드 부재 (C4)

각 시점 판정은 **prefix 만** 입력받는다:

```python
for end in range(min_candles, len(ordered) + 1):
    prefix = ordered[:end]        # ← 이 시점까지의 캔들만
    analysis, confluence = _judge(symbol, timeframe, prefix, prior, ...)
```

미래 캔들이 한 줄이라도 새면 **뒤쪽 봉이 앞쪽 판정을 바꾼다.** 그래서 증명은 등식으로 한다:

> **입력 구간을 절반으로 잘라도, 그 절반의 판정은 전체 입력 시와 완전히 동일해야 한다.**

강제 위치: `tests/test_directional_replay.py::test_prefix_invariance_proves_no_lookahead`.
이 등식이 깨진 채로 낸 대조표는 **전부 무효다.**

히스테리시스는 라이브와 같이 **매 봉 전진**한다(`prior_state` 체이닝). 채택 stance 는 경로
의존이므로 봉을 건너뛰면 기준선이 라이브를 대변하지 못한다.

## 3. 캔들은 어디서 오는가

**`stance_history_candles`** 테이블. Bitget 공개 히스토리 엔드포인트
(`/api/v2/mix/market/history-candles`)를 `refresh_stance_backtests` 가 일 1회 저비용으로
채운다(`app/backtest/stance_validation.py`).

- **크립토 캔들은 별도로 영속되지 않는다** — `market_candles` 테이블은 존재하지 않는다.
  실시간 경로는 스냅샷을 그때그때 받아 쓰고 버린다. 재판정 가능한 유일한 캔들 원장이
  `stance_history_candles` 다.
- 비어 있으면 스크립트는 **추정하지 않고 그 사실을 보고하고 종료한다.**

## 4. 사용법

```bash
cd backend

# ① 기준선 생성 — 판정 로직을 건드리기 전에 반드시 먼저
python3 scripts/directional_baseline_report.py ~/fomo_control_engine.db \
    --out docs/validation/baselines/directional_baseline.json

# ② Phase 적용 후 대조
python3 scripts/directional_baseline_report.py ~/fomo_control_engine.db \
    --compare docs/validation/baselines/directional_baseline.json
```

출력은 그대로 문서에 붙일 수 있는 마크다운이다. `--symbol` 로 심볼을 좁힐 수 있다.

### 기준선 해시

`records_sha256` 이 **기준선이 조용히 바뀌지 않았음**을 증명한다. 대조표의 신뢰는 전적으로
"같은 기준선인가"에 달려 있으므로, 해시가 바뀐 대조는 대조가 아니다.

`compare_to_baseline()` 이 내는 것:

| 항목 | 의미 |
|---|---|
| `phase_changed` · `stance_changed` | 판정이 바뀐 지점 수 |
| `directional_delta` | 방향 판정(`long_leaning`·`short_leaning`) 증감 |
| `monotone_decrease` | **보수화(C3) 준수 여부** — 방향 판정이 늘었으면 거짓 |

Phase 2·4 의 수용 기준이 "방향 판정이 줄어들기만 하고 늘어나지 않음"이므로,
`monotone_decrease == False` 는 그 자체로 정지 신호다.

## 5. 재현성 주의 — 벽시계

`build_chart_analysis` 는 `confirmed_chart_candles` 로 **미마감 캔들을 벽시계 기준으로**
버린다(`chart_analysis.py:169-186`). 따라서:

- 하네스는 **구간 전체가 이미 마감된 과거 캔들**에 대해서만 재현 가능하다.
- 막 생성 중인 캔들이 입력에 섞이면 **실행 시각에 따라 레코드 수가 달라진다.**

판정 시각(`generated_at`)은 그 봉이 마감된 직후로 고정한다. 벽시계를 쓰면 시간 감쇠
(recency)가 걸려 과거 재판정이 전부 stale 로 눌린다.

## 6. 이 하네스는 판정하지 않는다

`app/analyst/` · `app/structure/` 를 **호출만 한다.** 판정을 재구현하면 그 순간 기준선이
라이브가 아니라 하네스의 해석을 대변하게 된다. 회귀 테스트가 강제한다:

- `test_phase0_does_not_touch_judgment_logic` — `origin/main` 대비 두 디렉터리 diff 0줄
- `test_harness_lives_outside_judgment_packages` — 와이코프 내부 함수 직접 호출 금지

## 7. 중복 구현 검사 결과 (불변 규칙 2)

착수 전 확인에서 **기존 워크포워드 2건**을 발견했다. 새로 만들지 않고 관계를 정리했다:

| 기존 | 무엇을 재판정하나 | 왜 그대로 못 쓰나 |
|---|---|---|
| `app/backtest/replay.py` | 감지기 → 시그니처 → 성과 채점 | 방향 **판정**(국면·stance·커버리지)을 다루지 않는다 |
| `app/analyst/stance_history.py::replay_confirmed_stance_points` | 채택 stance 시계열 | 국면·엔진별 stance·커버리지를 **버린다** — Phase 1~5 의 대조축이 남지 않는다 |

이 하네스는 같은 워크포워드 구조를 쓰되 **판정의 내부를 기록한다.** 분석 창
(`REPLAY_ANALYSIS_WINDOW = 200`)은 `stance_history` 와 같은 값으로 맞췄다 — 라이브와 다른
창을 쓰면 기준선이 라이브를 대변하지 못한다.

## 8. Phase 0 에서 확인된 코드 사실

측정 없이도 코드 대조만으로 확정되는 것들이다. **Phase 4 착수 전 선결 항목**에 해당한다.

### 8.1 `_trend_payload` 의 산출 범위 — 박스 직전이 아니다

| 항목 | 값 | 위치 |
|---|---|---|
| 추세 측정 창 | **최근 28봉** (`candles[-12:]` vs `candles[-28:-12]`) | `wyckoff/engine.py:619-647` |
| 거래 박스 탐지 창 | **최근 60봉** (`RANGE_LOOKBACK = 60`) | `wyckoff/engine.py:12,163` |

> **추세 창이 박스 창 안에 완전히 들어 있다.**

즉 현행 `trend.direction` 은 "박스로 들어오기 전의 선행 추세"가 아니라 **박스 내부를 포함한
최근 흐름**이다. Phase 4 가 이 값을 그대로 게이트에 쓰면, 정석의 "선행 추세" 요건이 아니라
박스 내부 드리프트를 게이트하게 된다.

WO §8 작업 4가 요구한 선확인이 이것이며, 답은 **"박스 직전 구간이 아니다"** 다.
Phase 4 는 게이트 설계를 먼저 수정해야 한다 — 박스 시작 이전 구간에서 추세를 산출하는
경로가 필요하다. (이 문서는 사실만 기록한다. 설계 변경은 Phase 4 의 일이다.)

### 8.2 `trend` 는 계산되지만 국면 판정에 전달되지 않는다 (D1)

```
engine.py:65   trend = _trend_payload(ordered, {...})   ← 계산
engine.py:75   phase = _phase_from_events(events)        ← trend 를 인자로 받지 않는다
engine.py:99   result["trend"] = trend                   ← 출력에만 실린다
```

`trend` 는 **표시용으로만** 존재한다. 국면 판정은 이벤트만 본다.

### 8.3 커버리지는 confluence 에 존재하지 않는다 (E1)

`backend/app/analyst/confluence.py` 전체에서 `coverage` · `available_engines` · `missing`
등장 횟수 **0회**. 점수는 단순 합(`confluence.py:145`)이며, 침묵한 엔진은 양쪽에 0을
기여하고 끝난다.

## 8-4. 검증 창 앵커는 재판정 범위에 영향을 주지 않는다

`WO-FCE-WINDOW-ANCHOR-01` 이 도입한 창 앵커는 **검증 계수**(유효일·진입·채점 표본)에만 걸린다.
이 하네스는 `stance_history_candles` 를 읽고 판정을 재현할 뿐이므로 앵커와 무관하며,
**재시작 전 구간도 그대로 재판정할 수 있다.**

그 분리는 의도적이다. 기준선은 "엔진이 그때 무엇을 판정했는가"의 기록이고, 창은 "무엇을
검증 표본으로 셀 것인가"의 규칙이다. 창을 옮겼다고 과거 판정이 사라지면 Phase 대조가
재시작마다 끊긴다.

단, **판정 분포를 검증 표본 수와 나란히 놓을 때는 창 회차를 함께 적는다** — 재판정은 생애
전 구간이고 표본은 창 기준이라 분모가 다르다.

## 9. 실측은 아직 없다

이 문서는 **하네스와 코드 사실**까지다. 분포표(판정 가능 엔진 수 · 침묵 가중 비율 ·
불일치 빈도 · D1 발생률)는 **운영 DB 가 있는 호스트에서 §4 의 명령을 실행해야 나온다.**
이 저장소 컨테이너에서는 `stance_history_candles` 가 비어 있어 산출할 수 없다.

숫자를 추정해 채우지 않는다. 실측 결과는 `docs/validation/DIRECTIONAL_COVERAGE.md` 에
기록하며, **결정 3(임계 3종)은 그 분포를 본 뒤에 정한다.**

---

# 제2부 — 페이퍼 엔진 재판정 (WO-FCE-REPLAY-DEPTH-01 4-4)

> 정본 코드: `backend/app/validation/paper_replay.py` · `backend/app/validation/published_values.py`
> 실행 도구: `backend/scripts/paper_replay_report.py` (읽기 전용)
> 캔들 공급·영속화: [`CANDLE_SUPPLY.md`](CANDLE_SUPPLY.md)

제1부는 **방향 판정**을 재현한다. 제2부는 그 위에서 **거래**를 재현한다 — 진입 게이트 9종 ·
사이징 · 재진입 잠금 · 출구 사다리 · 손절 체결까지.

## 10. 무엇을 잇는가 (중복 구현 금지)

```
directional_replay._judge      판정 재현 (워크포워드 · 히스테리시스 체이닝)
      ↓
positions/simulator            액션 플랜 · 체크리스트
paper/service._paper_target_plan   ATR 단계 익절 · 실행 무효화가 · RR
      ↓
paper/policy                   evaluate_entry · plan_position_size · reentry_locked
                               evaluate_exit · apply_exit_decision
      ↓
risk_sizing_replay             R 정의 · PF · MDD · 손절 체결 집계
```

**셋 다 호출만 한다.** 게이트도 지표도 하네스가 다시 정의하지 않는다. 회귀가 강제한다
(`test_harness_does_not_reimplement_the_gates`).

## 11. 룩어헤드 (C6)

각 봉의 판정과 진입 결정은 `candles[:end]` 프리픽스만 입력받는다. 증명은 등식이다:

> **입력 구간을 잘라도, 그 구간에서 완결된 거래는 전체 입력 시와 완전히 동일해야 한다.**

강제 위치: `tests/test_paper_replay.py::test_prefix_invariance_proves_no_lookahead`.
절단점 이후에 청산된 거래는 잘린 입력에서 아직 열려 있으므로 비교 대상이 아니다 — 그것을
비교하면 "미래를 못 봤다"가 아니라 "미래가 아직 안 왔다"를 재게 된다.

구간 끝에 열려 있는 건은 **닫지 않는다.** 미실현 손익을 실현으로 적으면 표본이 실제보다
좋아지거나 나빠진다.

## 12. 손절 체결 반사실 — 봉 중간 터치 vs 종가

`RISK-SIZING-01` Phase 2 가 **크립토 봉 미보존**으로 포기한 반사실이다. 4-2 가 봉을
저장하면서 비로소 가능해졌다.

```python
# 라이브 현행 (policy._stop_breached)   종가만 본다
stop_fill="close"      close <= stop_price
# 반사실 (하네스가 대신 판정)             익절과 같은 규칙
stop_fill="intrabar"   bar.low <= stop_price   → 체결가는 무효화가
```

**`paper/policy.py` 는 한 줄도 바뀌지 않았다**(C3) — 반사실은 정책이 아니라 관측이다.

같은 봉 안에서 손절과 익절이 모두 닿았을 때 어느 쪽이 먼저였는지는 봉 데이터로 알 수 없다.
**리스크 관측에서는 나쁜 쪽을 가정하는 것이 정직하다** — 터치가 있으면 즉시 손절로 확정한다.

두 모드는 같은 캔들·같은 정책으로 돌린다. 그래서 결과 차이는 전부 체결 규칙 하나에
귀속된다(교란 없음).

## 13. 재현할 수 없는 입력은 **이름으로 남긴다**

과거 시점의 저장소 상태는 되돌릴 수 없다. 조용히 "통과"로 채우면 재판정이 라이브보다
관대해지고 그 사실이 결과 어디에도 안 남는다. `ReplayAssumptions` 가 전부 산출물에 실린다:

| 가정 | 왜 복원 불가 | 기본값 |
| --- | --- | --- |
| `signature_gate` | 과거 시점 시그니처 통계가 없다 | 통과 가정 (관측 등급 진입과 같은 취급) |
| 체크리스트 `funding` | 과거 펀딩률 히스토리가 저장되지 않는다 | 통과로 센다 |
| 체크리스트 `volume` | `volume_state` 는 실체결(trade_flow)이 있어야 판정된다 | 통과로 센다 |
| 파생 히스토리 | 과거 펀딩·OI·청산이 없다 | **대체하지 않는다** (`not_included`) |

### 이것이 드러낸 구조적 공백

체크리스트 6항목 중 `funding` · `volume` 두 개는 **OHLCV 로 복원되지 않는다.** 그래서
재판정에서 평가 가능한 항목은 최대 4개이고, `min_checklist_total=5` 는 **구조적으로 통과
불가**다.

`unavailable_checklist_policy="block"` 을 고르면 재판정 진입이 **정확히 0건**이 된다 —
그것이 저장 공백의 크기다(회귀: `test_blocking_policy_makes_the_structural_gap_visible`).
기본값은 `count_as_pass` 이며, 가정으로 채운 항목이 **거래 건별로** `checklist.assumed_items`
에 기록된다.

> 이 공백을 없애려면 펀딩 히스토리와 체결 버킷도 영속화해야 한다. **이 WO 범위 밖이다.**

## 14. 발표값 자동 대조 (Phase 3-5 오프셋 사고의 재발 방지책)

`RISK-SIZING-01` Phase 1~4 의 반사실은 전부 **커밋되지 않은 임시 스크립트**로 산출됐고,
그래서 Phase 3-5 의 합계를 재현할 수 없게 됐다(오프셋 net −1.902R, 두 행에서 동일).

`app/validation/published_values.py` 가 발표값과 **그 재현 상태**를 함께 등록한다:

| 계층 | 대상 | 어디서 강제되나 |
| --- | --- | --- |
| 픽스처 기준선 | 커밋된 캔들(`tests/fixtures/replay_candles_4h.json`) 위의 재판정 | **CI** |
| 원장 기준선 | 라이브 `paper_trades` 위의 반사실 | 호스트 (`--compare-published`) |

### 재현 안 되는 값을 지우지 않는다

`reproduces=False` 항목을 레지스트리에서 빼면 사고가 기록에서 사라진다. 대신 **알려진
오프셋을 등록하고, 그 오프셋이 변하면 실패**시킨다.

- 오프셋 그대로 → 알려진 미해결 항목. 새 드리프트 없음
- 오프셋 변화 → **조용한 드리프트다.** 지금 잡는다

CI 에는 DB 가 없다. 그래서 원장 기준선은 `requires_database=True` 로 표시되고 CI 는 **등록
위생만** 강제한다(모든 항목이 정본 문서를 가리키는가 · 재현 안 되는 항목에 오프셋이 있는가).
건너뛴 항목은 `skipped` 목록에 남는다 — **조용히 건너뛰지 않는다.**

### 재판정 CI 기준선

`tests/fixtures/replay_candles_4h.json` (180봉 합성 4시간봉) 위에서:

| 지표 | `stop_fill=close` | `stop_fill=intrabar` |
| --- | ---: | ---: |
| 판정 지점 | 81 | 81 |
| 청산 표본 | 11 | 11 |
| grossR | +10.3398 | +10.5000 |
| 비용R | 1.6118 | 1.6049 |
| netR | +8.7280 | +8.8951 |
| PF | 3.5152 | 4.8490 |
| MDD (USDT) | 8.6754 | 5.7775 |

> ⚠️ **합성 캔들이다. 엔진 성적이 아니라 경로 재현성의 기준선이다**(C9). 이 표의 숫자를
> 엔진 성능으로 인용하면 안 된다 — 상승 편향이 들어간 인위적 시계열이다.

재판정 경로가 조용히 바뀌면 이 표에서 즉시 실패한다.

## 15. 파라미터 스윕 — "엔진 고도화"의 실행 수단

```bash
cd backend
PYTHONPATH=. python3 scripts/paper_replay_report.py --database ~/fomo_control_engine.db --sweep
```

기본 축(`scripts/paper_replay_report.py::SWEEP_AXES`)은 **한 번에 하나씩** 움직인다 —
재진입 잠금 · 리스크 예산 · TP2 배수 · RR 기준. 여러 축을 동시에 움직인 행만 보면 무엇이
효과였는지 영원히 알 수 없다(AGENTS.md "승률 개선안은 한 번에 하나씩").

> **스윕은 과거 데이터다.** 과최적화 위험이 있고 라이브 확인을 대체하지 않는다. 스윕으로
> 고른 설정은 반드시 전방 라이브로 확인해야 하며, **그 확인 규칙은 결과를 보기 전에**
> 정해야 한다. 산출물의 `overfit_warning` 이 매 실행 이 문장을 다시 찍는다.

## 16. 사용법

```bash
cd backend

# ① 전 구간 재판정 + 손절 체결 반사실
PYTHONPATH=. python3 scripts/paper_replay_report.py --database ~/fomo_control_engine.db

# ② 파라미터 스윕
PYTHONPATH=. python3 scripts/paper_replay_report.py --database ~/fomo_control_engine.db --sweep

# ③ 발표값 대조 — 어긋나면 종료 코드 1
PYTHONPATH=. python3 scripts/paper_replay_report.py --database ~/fomo_control_engine.db --compare-published
```

`stance_history_candles` 가 비어 있으면 **추정하지 않고 그 사실을 보고하고 종료한다.**
캔들을 채우는 잡은 `replay_history_backfill`(기본값 꺼짐)이며 정본은 [`CANDLE_SUPPLY.md`](CANDLE_SUPPLY.md).

## 17. 표본 규모 — 표본 수이지 실적이 아니다 (C9)

재판정 판정 지점 수는 `봉 수 − 최소 캔들(100) + 1` 이다. 심볼당 2,196봉이면 **2,097 판정
지점**이고, 라이브 하루 12건 기준 **약 175일치**다. 10심볼이면 1,750일치.

| | 지금 | 재판정 이후 |
| --- | --- | --- |
| 파라미터 1개 검증 | 4주 대기 | 즉시 |
| 표본 | 12건/일 | 수백 건/실행 |
| 반증 | 라이브 결과 대기 | 스윕으로 즉시 |
| 기준선 재현 | **불가**(Phase 3-5) | CI 가 강제 |

**이 배수는 "얼마나 빨리 검증할 수 있는가"이지 "얼마나 잘 벌었는가"가 아니다.** 청산 표본
수는 판정 지점 수보다 훨씬 작다 — 게이트 9종을 통과한 건만 거래가 된다.

## 18. 실측은 아직 없다

제2부도 **하네스와 CI 기준선**까지다. 라이브 원장·저장 히스토리 위의 숫자(심볼별 재판정
표본 · 손절 체결 반사실 · 스윕 결과)는 **운영 호스트에서 §16 을 실행해야 나온다.**
이 저장소 컨테이너에는 `stance_history_candles` 가 없고 거래소 네트워크도 닿지 않는다.

숫자를 추정해 채우지 않는다.
