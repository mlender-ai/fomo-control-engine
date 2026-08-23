# 캔들 공급 (Candle Supply)

> WO-FCE-REPLAY-DEPTH-01 4-1·4-2 정본. **게이트 임계 diff 0줄**(C1) · **자산군 분류 diff 0줄**(C2).
>
> 실사 2026-08-21 · Bitget USDT-FUTURES · `stance_history_candles` 5,577행

---

## 0. 결론 — 199 는 provider 탓이 아니었다

`STOCK-UNBLOCK-01` 은 `provider.py:689 limit=200` 에서 미확정봉 1개가 빠져 199가 된다고 봤다.
**절반만 맞다.** 실측하니 `get_ohlcv(limit=200)` 은 **모든 심볼에 정확히 200개**를 준다 —
AAPLUSDT 포함. 줄어드는 곳은 그 뒤다.

```
get_ohlcv(200)  →  200
  미확정봉 제거  →  199
  세션 필터      →  146      ← stock/index 만. marketdata/sessions.py:100-105
게이트 요구      →  >= 200
```

`filter_analysis_candles` 가 `stock`·`index` 에서만 `session == "closed"` 봉을 떨어낸다.
실측 손실률 **약 30%**:

| 심볼 | 자산군 | 원본 | 세션 필터 후 | 손실 |
| --- | --- | --- | --- | --- |
| AAPLUSDT | stock | 2,161 | 1,507 | 30.3% |
| QQQUSDT | index | 1,783 | 1,237 | 30.6% |
| INTCUSDT | stock | 2,083 | 1,453 | 30.2% |
| BTCUSDT | crypto | 2,196 | 2,196 | **0%** |

> **`stage2_template` 은 stock·index 에만 걸린다. 그리고 캔들이 깎이는 자산군이 정확히
> 그 둘이다.** 캔들이 줄어드는 쪽에 ≥200 을 요구하는 이중 구속이었다.

라이브 경로 재현 (현행):

```
AAPLUSDT (stock)    200 → 199 → 146    조기 반환
INTCUSDT (stock)    200 → 199 → 146    조기 반환
INTCUSDT (crypto)   200 → 199 → 199    조기 반환   ← 자산군이 맞아도 1개 부족
```

**어느 자산군에서도 통과가 불가능했다.** crypto 는 199로 1개 모자라고, stock 은 세션 필터로
146까지 떨어진다.

---

## 1. AAPLUSDT 140 의 원인 — 상장 기간이 아니다

`STOCK-UNBLOCK-01` 은 "신규 상장인가 공급 실패인가"를 미상으로 남겼다. **둘 다 아니다.**

```
get_history_ohlcv_async("AAPLUSDT", "4h", 2196)  →  2,161봉 (360일)
```

**히스토리는 충분히 있다.** 140은 최근봉 경로 + 세션 필터의 결과이고, 깊은 로더를 쓰면
1,507봉(세션 필터 후)이 나온다 — 게이트 요구의 7배다.

---

## 2. 깊은 로더는 이미 있었고 값싸다 (4-1 작업 4)

| 심볼 | 요청 | 수신 | 소요 | 기간 |
| --- | --- | --- | --- | --- |
| BTCUSDT | 2,196 | 2,196 | 1.59s | 366일 |
| AAPLUSDT | 2,196 | 2,161 | 1.63s | 360일 |
| INTCUSDT | 2,196 | 2,083 | 1.79s | 347일 |
| HYPEUSDT | 2,196 | 2,196 | 1.60s | 366일 |
| BASEDUSDT | 2,196 | 865 | 0.76s | 144일 |
| SPCXUSDT | 2,196 | 439 | 0.67s | 73일 |

**심볼당 약 1.6초.** 라이브 유니버스 13종이면 21초다 — C8 예산에 여유가 크다.
SPCX·BASED 는 실제로 신규 상장이지만 그래도 200봉을 넘는다.

---

## 3. 저장 실태 — 한 잡의 부산물이었다 (D3 확정)

```
stance_history_candles   3심볼 · 5,577행
  BTCUSDT   4h  2,391   2025-07-17 ~ 2026-08-19
  ETHUSDT   4h  2,391   2025-07-17 ~ 2026-08-19
  SOXLUSDT  4h    795   2026-04-09 ~ 2026-08-19
```

라이브 유니버스 13종과의 **교집합은 SOXLUSDT 하나**였다.

기록 경로는 `backtest/stance_validation.py:68` **한 줄뿐**이고 대상이 `DEFAULT_SYMBOLS`
(BTC·ETH·SOL·XRP·DOGE)다. 그런데 저장된 것은 BTC·ETH·SOXL 이다 — `stance_backtest` 가
일일 잡이고 거의 돌지 않아 **부분 커버리지**만 남았다.

> `RISK-SIZING-01` Phase 2 가 손절 체결 반사실을 포기하고, `POSITION-VIEW-01` 이 국면-추세
> 상충률을 산출 불가로 남긴 것이 **모두 이 한 줄 때문**이다.

---

## 4. 배선 (4-2) — 대상 선정 · 증분 · 리텐션만 추가했다

정본: [`app/validation/history_backfill.py`](../../backend/app/validation/history_backfill.py)

수집·저장은 **기존 경로를 그대로 재사용**한다(중복 구현 금지) —
`get_history_ohlcv` + `upsert_stance_history_candles`. 추가한 것은 셋뿐이다:

| 항목 | 동작 |
| --- | --- |
| 대상 선정 | `paper_universe(repo)` — 라이브가 실제로 평가하는 심볼 |
| **증분** | 저장된 마지막 봉 이후 + 겹침 3봉만 요청. 매번 2,196봉을 다시 받지 않는다 |
| **리텐션**(C7) | 심볼당 2,196봉 상한. 오래된 것을 떨어내고 최근을 남긴다 |
| 실패 처리 | 심볼별로 사유와 함께 결과 반환 — 한 심볼 실패가 나머지를 굶히지 않는다 |
| 조기 반환 | 대상 0이면 `effective_run=False` — "돌지만 안 돈다"를 성공으로 세지 않는다 |

**잡은 격리 실행기에서 돈다**(C8). `WORKER-HANG-02` 2-3 이 만든 `_HEAVY_JOBS` 에 등록했다 —
심볼당 1.6초 × 최대 25심볼이 기본 풀을 먹으면 표본 생산 잡이 굶는다.

주기는 **6시간**이다. 4시간봉 히스토리를 그보다 자주 갱신할 이유가 없다.

### 되돌리기 (C5)

```
FCE_REPLAY_HISTORY_BACKFILL_ENABLED=false     ← 기본값. 켜지 않으면 수집 0
FCE_REPLAY_HISTORY_BACKFILL_MAX_SYMBOLS=25    ← C8 안전판
FCE_REPLAY_HISTORY_RETENTION_BARS=2196        ← C7
```

---

## 5. 4-3 을 하지 않았다 — 순서 때문이다

**분석 페이로드 확대(휴면 게이트 활성화)를 적용하지 않았다.** WO §2 가 경고한 그 위험이고,
두 경로 모두 지금 하면 안 된다:

| 방안 | 문제 |
| --- | --- |
| provider 공유 한도 인상 | **모든 감지기의 입력 길이가 바뀐다.** `_active_fvgs` 는 `range(2, len(candles))` 를 돈다 — FVG·와이코프·유동성 출력이 전부 달라진다. 코드 diff 0줄로 C4 를 위반한다 |
| stage2 전용 장기 시계열 | 옳은 방향. 그러나 **자산군 분류 수리가 선행**이다 |

**분류 수리가 선행인 이유**: 지금 stock·index 는 3심볼이라 비용이 무시할 만하지만,
`STOCK-UNBLOCK-01` 판정 D(RWA 294건 중 ~262종이 crypto 오분류)를 고치면 **289심볼**이 되어
조회 비용이 100배가 된다. 순서를 뒤집으면 큐를 다시 굶긴다.

그리고 C2 가 이 WO 에서 분류 수리를 금지한다 — **지금 4-3 을 하면 알고 있는 잘못된
코호트 위에서 게이트를 깨우는 것**이 된다.

권고 순서는 `STOCK_TRACK.md` §3 과 같다: ① 분류 → ② 유효 모집단에서 재측정 →
③ 캔들 공급 → ④ 조건 2·3·4 의 사상 첫 통과율.

---

## 5-0. 분류 수리 시 세션 필터 영향 (WO-FCE-ASSET-CLASS-01 3-4)

자산군 분류를 고치면 약 262종이 `crypto → stock` 으로 옮겨가고, 그 순간 **세션 필터를
새로 받는다**. §0 실측 손실률 30% 를 적용하면:

```
200 ÷ (1 − 0.30) = 286봉      ← stage2_template 200봉 요건을 채우기 위한 공급량
```

최근봉 경로(200)로는 **불가능**하다. 깊은 로더(2,196봉)를 쓰면 여유가 크다:

| 심볼 | 깊은 로더 | 세션 필터 후(추정) | 200 요건 |
| --- | ---: | ---: | --- |
| AAPLUSDT | 2,161 | 1,507 (실측) | 충족 (7.5배) |
| INTCUSDT | 2,083 | 1,453 (실측) | 충족 (7.3배) |
| BASEDUSDT | 865 | ~605 | 충족 |
| SPCXUSDT | 439 | ~307 | 충족 |

**신규 상장 심볼도 세션 필터 후 200봉을 넘는다.**

> 3-4 의 답: **캔들은 분류 수리의 병목이 아니다.** `REPLAY-DEPTH-01` 4-3 의 실제 선행
> 조건은 캔들이 아니라 [`ASSET_CLASS.md`](ASSET_CLASS.md) §2 의 실적 공급원 결정이다.

산식과 회귀: `app/marketdata/asset_class_audit.py::reclassification_impact` ·
`tests/test_asset_class.py::test_required_supply_accounts_for_the_session_filter`.

---

## 5-1. 리텐션 정정 — 보고와 실제가 반대였다 (C7)

§4 의 리텐션 배선에 결함이 있었다. `apply_retention` 은 **upsert 대상 목록**을 잘라내는데
`upsert_stance_history_candles` 는 INSERT/UPDATE 만 한다 — **이미 저장된 오래된 행은 지워지지
않는다.**

실측(수리 전):

```
1회차  리텐션 100  50봉 저장                          표: 50행
2회차  리텐션 10   5봉 수집 → pruned=45 보고           표: 55행   ← 늘었다
```

**보고와 실제가 반대 방향인 리텐션은 없는 것보다 나쁘다** — 있다고 믿게 만들기 때문이다.
DB 12.8GB 선례가 정확히 "리텐션 DELETE 는 있었으나 회수가 없었다"였고, 이번은 그보다 한 칸
앞이다(DELETE 자체가 없었다).

### 수리

| 축 | 무엇 |
| --- | --- |
| 저장소 삭제 | `repo.prune_stance_history_candles(symbol, timeframe, keep_bars)` — 실제 `DELETE` |
| 수집 경로 | `history_backfill` 이 upsert 직후 저장소에 삭제를 시킨다 |
| 정기 경로 | `database_retention` 이 같은 상한을 건다 — **잡이 꺼져 있는 동안에도**(기본값이 꺼짐이다) |
| 보고 | `pruned` = **실제 삭제 행 수** · `trimmed` = 애초에 쓰지 않은 봉. 둘을 합치지 않는다 |

삭제를 지원하지 않는 저장소에서는 `pruned=0` 을 보고한다 — **지웠다고 적지 않는다.**

회귀: `tests/test_replay_depth.py::test_retention_actually_deletes_rows_that_are_already_stored` ·
`::test_scheduled_retention_caps_the_table_while_the_job_is_off`.

---

## 5-2. 재판정은 여기서 이어진다

봉이 저장되면 그 위에서 페이퍼 엔진을 재실행할 수 있다 — 진입 게이트 9종 · 사이징 ·
재진입 잠금 · 출구 사다리 · 손절 체결까지. 그것이 4-4 이며 정본은
[`REPLAY_HARNESS.md`](REPLAY_HARNESS.md) 제2부다.

`RISK-SIZING-01` Phase 2 가 **크립토 봉 미보존**으로 포기한 손절 체결 반사실(봉 중간 터치 vs
종가)이 그 하네스에서 종결된다 — [`STOP_EXECUTION.md`](STOP_EXECUTION.md) §6.

---

## 6. 금지

- `provider` 공유 캔들 한도 인상으로 게이트를 깨우기 — 모든 감지기 출력이 바뀐다
- 자산군 분류 수리 전에 캔들 공급을 확대하기 — 비용 100배 · 큐 기아 재발
- 리텐션 없이 저장 확대 — DB 12.8GB 선례
- 증분 없이 매 실행 전량 재수집 — 심볼당 1.6초가 25심볼이면 40초, 유니버스가 커지면 예산 초과
- `stage2_template` 임계 변경 (C1) — 조건 2·3·4 는 아직 한 번도 측정되지 않았다
