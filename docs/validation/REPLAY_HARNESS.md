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

## 9. 실측은 아직 없다

이 문서는 **하네스와 코드 사실**까지다. 분포표(판정 가능 엔진 수 · 침묵 가중 비율 ·
불일치 빈도 · D1 발생률)는 **운영 DB 가 있는 호스트에서 §4 의 명령을 실행해야 나온다.**
이 저장소 컨테이너에서는 `stance_history_candles` 가 비어 있어 산출할 수 없다.

숫자를 추정해 채우지 않는다. 실측 결과는 `docs/validation/DIRECTIONAL_COVERAGE.md` 에
기록하며, **결정 3(임계 3종)은 그 분포를 본 뒤에 정한다.**
