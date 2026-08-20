# 발견 게이트 (Discovery Gate)

> WO-FCE-DISCOVERY-UNBLOCK-01 정본. **임계값 diff 0줄. 비순환 게이트 diff 0줄.**
>
> 실사 2026-08-19 · 발견 534건(최근 6시간) · crypto 387 · stock 83 · index 64

---

## 0. 결론 먼저 — WO 의 전제가 절반만 맞았다

WO 는 "`backtest_sample 0 < 30` 이 순환이고 `backtest_win_1r_ci_low` 도 같은 뿌리"라고 봤다.
**crypto 에서는 그렇지 않다.**

| 자산군 | 발견 | `gate_passed` | `backtest_sample` 실패 | `backtest_win_1r_ci_low` 실패 |
| --- | --- | --- | --- | --- |
| **crypto** | 387 | **0** | 93 (24%) | **387 (100%)** |
| stock | 83 | 0 | 32 | 83 |
| index | 64 | 0 | 64 | 64 |

crypto 387건 중 **294건(76%)은 표본이 이미 충분하다** — N=214~548. CI 가 정상적으로 계산되며,
그 값이 이렇게 말한다:

| 시그니처 | N | 승률 | CI |
| --- | --- | --- | --- |
| `liquidity:sweep_high:strong:short` | 548 | 41.6% | [37.8, **46.2**] |
| `liquidity:sweep_low:strong:long` | 451 | 33.0% | [28.8, **37.7**] |
| `liquidity:htf_sweep_high:strong:short` | 427 | 38.2% | [33.7, **42.6**] |
| `liquidity:sweep_high:mid:short` | 368 | 38.3% | [33.4, **43.2**] |
| `liquidity:htf_sweep_low:mid:long` | 349 | 37.3% | [32.4, **42.7**] |

```
표본 >= 30 인 294건의 CI 하한:  최소 26.2% · 중위 31.4% · 최대 37.8%   (임계 50%)
50% 이상인 건:  0 / 294
CI 를 계산할 수 없는 건: 0 / 294
```

**CI 상한조차 46.2% 를 넘지 못한다.** 이것은 순환이 아니다. 표본 수백 건으로 측정한 결과가
"이 시그니처들은 이긴 적이 없다"인 것이다.

> **그래서 `backtest_win_1r_ci_low` 를 요구에서 빼지 않았다.** 그것을 빼면 승률 33~42% 로
> **측정된** 시그니처를 통과시키는 것이고, 그것은 정합 수리가 아니라 완화다(C1·C2·C10 위반).
> WO §1 이 "완화는 기준을 낮춰 통과를 만드는 것"이라고 정의한 바로 그 행위다.

---

## 1. 그럼 진짜 순환은 어디에 있었나 — 게이트가 두 일을 겸했다

`gate_passed` 는 원래 **알림 발송 자격**이다:

```python
scout/universe.py:107   gate_passed=gate.quality_passed
scout/universe.py:93    status = "alerted" if gate.dispatch_allowed else "stored"
scout/universe.py:243   _reason("daily_alert_limit", ...)      # 같은 경로
scout/universe.py:244   _reason("symbol_cooldown", ...)        # 같은 경로
```

그런데 페이퍼 엔진이 **평가할 심볼**을 같은 플래그로 정한다:

```python
paper/service.py   paper_universe()
    → repo.list_recent_gate_passed_universe_discoveries(limit=500)
```

**두 목적이 한 술어에 얹혀 있었다.** 그리고 그것이 순환을 만든다 —
이 레포는 진입 경로에서 같은 순환을 이미 끊었기 때문이다:

```python
paper/policy.py:158   # 시그니처 검증을 진입 조건에서 뺀다(record_only) — 검증 대상을
paper/policy.py:159   # 검증 조건으로 삼으면 표본이 영원히 생기지 않는다
```

> **진입은 시그니처 검증을 요구하지 않는데, 그 심볼을 보기까지가 시그니처 검증을 요구했다.**
> 진입 게이트에서 뺀 조건이 **유니버스 급유 단계에 그대로 남아 있었다.** 그것이 이 WO 의
> 실제 대상이다.

이것을 푸는 것은 완화가 아니다 — 진입 판정은 페이퍼 자신의 게이트 9종이 그대로 하고,
시그니처 승률은 애초에 진입 조건이 아니었다. **바뀌는 것은 "무엇을 평가해 볼지"뿐이다.**

---

## 2. 순환/비순환 분류표 (3-1)

| 게이트 | 판정 | 근거 | 조치 |
| --- | --- | --- | --- |
| `backtest_sample` | **순환** | 통과해야 표본이 쌓이고, 표본이 있어야 통과한다 | **평가 급유에서만 미룸** |
| `backtest_win_1r_ci_low` | **조건부 순환** | 표본 0 일 때만 순환. crypto 76% 는 표본 충분이며 그 CI 가 실패를 말한다 | **알림 자격에서 유지. 평가 급유에서만 미룸** |
| `liquidity_floor` | 비순환 | 24시간 거래대금. 시장 데이터로 독립 충족 | **그대로 요구** (crypto 241건 계속 거름) |
| `confidence` | 비순환 | 분석 신뢰도. 독립 산출 | **그대로 요구** (51건 거름) |
| `stage2_template` | 비순환 | `structure/candidates/engine.py:38` 실제 구조 조건 | **그대로 요구** (주식·지수 0건 통과) |
| `signature_lifecycle_state` | 비순환 | 강등·격리 상태. 독립 | **그대로 요구** |
| `live_backtest_divergence` | 비순환 | 라이브/백테스트 괴리 플래그 | **그대로 요구** |
| `earnings_window` | 비순환 | 실적 D-1~D+1 | **그대로 요구** |
| `daily_alert_limit` · `symbol_cooldown` | **발송 전용** | 평가 자격과 무관 | 평가 판정 대상 아님 |

미루는 것은 **두 개뿐**이며, 그것도 **평가 급유 경로에서만**이다. 알림 자격(`gate_passed`)은
한 줄도 바뀌지 않았다.

정본 술어: [`app/scout/discovery_gate.py`](../../backend/app/scout/discovery_gate.py)

---

## 3. 해제 규모 — 폭증이 아니다 (3-1 작업 4)

관측 등급을 적용했을 때 (최근 6시간 발견 534건 기준):

| 자산군 | 발견 | 현행 통과 | **관측 등급 통과** | 고유 심볼 |
| --- | --- | --- | --- | --- |
| crypto | 387 | 0 | **120** | **12** |
| stock | 83 | 0 | **0** | 0 |
| index | 64 | 0 | **0** | 0 |

새로 들어오는 심볼: `ADAUSDT` `BNBUSDT` `DELLUSDT` `DOGEUSDT` `INTCUSDT` `IRENUSDT`
`LINKUSDT` `MRVLUSDT` `RKLBUSDT` `SOLUSDT` `TSMUSDT` `XRPUSDT`

**평가 유니버스 3종 → 최대 15종**(watchlist 3 + 12). 500종이 쏟아지지 않는다 —
비순환 게이트가 유동성 241건·신뢰도 51건을 계속 거르기 때문이다.
주식·지수는 `stage2_template` 로 0건이며 **손대지 않았다**(C2).

---

## 4. 배선 — 요구에서 기록으로 (3-2)

```python
scout/discovery_gate.py   OBSERVATION_DEFERRED_GATES = {"backtest_sample", "backtest_win_1r_ci_low"}
scout/discovery_gate.py   observation_gate_passed(reasons)      # 비순환 전부 요구
db/repository/scout.py    list_recent_observation_universe_discoveries()
paper/service.py          paper_universe(repo, observation_feed=...)
paper/service.py          observation_only_universe(repo, validated=...)
```

| 항목 | 상태 |
| --- | --- |
| `universe_backtest_min_sample` | **30 — 변경 없음** (C1) |
| `universe_backtest_min_ci_low_pct` | **50.0 — 변경 없음** (C1) |
| `stance_backtest_sample_floor` | **30 — 변경 없음** (C1) |
| 비순환 게이트 | **diff 0줄** (C2) |
| 미룬 게이트의 판정값 | **계속 산출·저장** (C3) — `gate_reasons` 에 그대로 남는다 |
| `gate_passed` (알림 자격) | **의미·값 변경 없음** |
| 진입 게이트 9종 | **diff 0줄** (C6) |

`discovery_gate.py` 를 별도 leaf 모듈로 둔 이유는 저장소 계층이 그 술어를 그대로 써야 하기
때문이다 — `scout/universe.py` 를 임포트하면 `structure/`·`review/` 를 끌어와 순환이 생긴다.
`validation/window_anchor.py` 가 같은 이유로 분리돼 있고 그 주석이 선례다.
**술어를 SQL 과 파이썬 두 곳에 두지 않는다** — 둘로 두면 하나는 반드시 어긋난다.

### 되돌리기 (C4)

```json
backend/app/paper/params/crypto-v2.json
  "observation_universe_enabled": false        ← 한 값
```

환경변수 `FCE_PAPER_OBSERVATION_UNIVERSE_ENABLED` 로도 덮을 수 있다. 파일 우선이며,
키가 없으면 설정 기본값(**False = 기존 동작**)이다.

---

## 5. 사후 채점 (3-3 · C5) — 이것 없이 4번만 하면 완화다

이 경로로 유입된 심볼의 거래는 원장에 표시된다:

```python
entry_evidence.universe_source = "observation_unvalidated" | "validated"
```

그리고 성적이 **분리 집계**된다:

```
GET /api/system/paper/scoreboard  →  universe_sources
  { "validated":              {trade_count, net_pnl_usdt, win_rate_pct, profit_factor, ...},
    "observation_unvalidated": {...},
    "note": "관측 등급 통과는 **평가 자격**이며 품질 검증이 아니다" }
```

과거 원장에는 플래그가 없다 — 없으면 `validated` 로 센다. 관측 등급으로 오분류하면
성적이 왜곡되기 때문이다(회귀 테스트가 고정).

### 사후 채점은 선정 기준으로 역류하지 않는다

여기서 나온 승률을 유니버스 선정에 되먹이면 그 순간 "성적 좋은 심볼만 골라 평가한다"가 되고
표본이 편향된다. `OBSERVATION-INTEGRITY-01` Phase 5 가 선정 기준에 승률을 넣지 않고
사후 채점으로만 쓴 것과 같은 이유다. **이 집계는 보고 전용이다.**

### 복귀 조건 — **결과 확인 전에 확정한다**

```
시그니처별 페이퍼 표본이 universe_backtest_min_sample(30)에 도달하면
그 시그니처는 관측 등급 유입에서 빠지고 backtest_win_1r_ci_low 요구로 복귀한다.
```

근거: 표본이 차면 미룰 이유가 사라진다. 미룬 것은 "표본이 없어서 판정할 수 없다"였고,
표본이 생기면 판정할 수 있다. 강등·보수화 방향이므로 자율 실행이 허용된다(비대칭 자율).

**이 조건은 위 §0 의 실측을 보고 정한 것이 아니다** — 미루는 근거 자체가 "표본 부재"이므로
복귀 조건이 "표본 확보"인 것은 정의상 따라온다.

---

## 6. 이 문서가 바꾸지 않은 것

```
발견 게이트 임계값        전부 변경 없음 (C1)
비순환 게이트             diff 0줄 (C2)
gate_passed 의 의미·값    변경 없음 — 알림 자격은 그대로
진입 게이트 9종           diff 0줄 (C6)
analyst/ · structure/     diff 0줄 (C6)
사이징·잠금·출구·손절     diff 0줄 (C7)
봉인·실계좌               diff 0줄 (C8)
```

---

## 7. 금지

- 임계값 하향 — `universe_backtest_min_sample`·`universe_backtest_min_ci_low_pct`·품질 문턱
- **`backtest_win_1r_ci_low` 를 알림 자격에서 빼기** — §0 대로 그것은 완화다
- 비순환 게이트 해제 — `stage2_template`·`liquidity_floor`·`confidence` 포함
- 미룬 게이트의 산출·저장을 생략하기 (기록은 유지된다)
- 사후 채점 없이 유니버스만 넓히기
- **통과 건수·유니버스 확대를 품질 개선으로 보고하기** — 표본이 쌓이기 시작한 것일 뿐이다
- 사후 채점 결과를 유니버스 선정에 되먹이기

---

## 8. 주식 경로 (WO-FCE-STOCK-UNBLOCK-01 · 2026-08-19)

§3 에서 "stock 83 → 0 · index 64 → 0" 이었고 `stage2_template` 을 비순환으로 분류해
손대지 않았다. **그 분류는 옳았지만 이유가 달랐다.**

`stage2_template` 은 비순환이 맞다. 그런데 **한 번도 평가된 적이 없다** —
게이트가 캔들 200개를 요구하는데 분석 파이프라인이 **199개**를 공급한다
(`provider.py:689 limit=200` → 미확정 봉 1개 제거). BTC 도 199개다.

즉 §2 분류표의 `stage2_template` 행은 "실제 구조 조건"이 맞지만, 그 조건이 **작동한 적이
없으므로** 0% 통과율은 조건의 엄격함이 아니라 데이터 결함이다.

그리고 이 경로에는 두 번째 결함이 있다 — **자산군 오분류**. Bitget RWA 294건 중
허용목록에 든 27개만 `stock`/`index` 로 분류되고, 나머지 ~262종(INTC·MRVL·DELL·TSM 등)은
`crypto` 로 취급돼 `stage2_template` 과 `earnings_window` 를 **건너뛴다.**

> §3 에서 "관측 등급 통과 12심볼"로 유입시킨 DELL·INTC·MRVL 은 **토큰화 주식**이다.
> 크립토 유니버스 확대의 일부는 주식을 크립토로 착각해서 얻은 것이다.

정본: [`STOCK_TRACK.md`](STOCK_TRACK.md)
