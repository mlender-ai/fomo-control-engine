# 주식 트랙 (Stock Track) — 표본 0 진단

> WO-FCE-STOCK-UNBLOCK-01 정본. **`active` 판정 로직 diff 0줄. 임계값 diff 0줄. 크립토 경로 diff 0줄.**
>
> 실사 2026-08-19 · 발견 534건(6시간) · Bitget 계약 759건 · 카탈로그 759건

---

## 0. 판정 — **A(데이터 결함)** 이고, 그보다 큰 결함을 함께 찾았다

| 판정 | 결과 |
| --- | --- |
| **A. 데이터 결함** | ✅ **확정.** `stage2_template` 은 **어떤 심볼에서도 한 번도 평가된 적이 없다** |
| B. 조건 부적합 | 판정 불가 — 조건 2·3·4 가 실행된 적이 없다 |
| C. 정당한 거부 | 해당 없음 |
| **(신규) D. 자산군 오분류** | ✅ **확정.** RWA 주식 ~262종이 crypto 로 거래되고 있다 |

**그리고 "주식 표본 0"은 사실이 아니다.** 주식 표본은 이미 5건 존재하며, `crypto` 라고
잘못 표시돼 있었다.

---

## 1. 판정 A — 게이트가 요구하는 캔들이 파이프라인에 없다

```python
structure/candidates/engine.py:39   if len(ordered) < 200:  →  조기 반환 (조건 2·3·4 미평가)
exchange/bitget/provider.py:689     self.get_ohlcv(normalized, timeframe, limit=200)
```

`limit=200` 으로 받아 **미확정 봉 1개를 버리면 199개**가 분석에 도달한다.
게이트는 **200개 이상**을 요구한다. **1개 차이로 항상 조기 반환된다.**

실측 (`/api/scout/{symbol}/analysis` · `analysis["candles"]` 길이):

| 심볼 | 자산군 | 캔들 수 | 게이트 요구 | 결과 |
| --- | --- | --- | --- | --- |
| AAPLUSDT | stock | **140** | ≥ 200 | 조기 반환 |
| INTCUSDT | crypto | **199** | ≥ 200 | 조기 반환 |
| BTCUSDT | crypto | **199** | ≥ 200 | 조기 반환 |

> **BTC 도 199개다.** 이것은 주식 전용 문제가 아니라 **파이프라인 전체의 상한**이다.
> `stage2_template` 은 크립토에 안 걸리므로(자산군 조건) 지금까지 드러나지 않았을 뿐,
> **정배열·기울기·−25% 세 조건은 이 저장소에서 한 번도 실행된 적이 없다.**

`toss_candles` 는 충분하다(205심볼 전부 200개 이상 · 중위 4,484개). **저장이 아니라
분석 페이로드 공급이 문제다** — 다른 경로다.

### 조건별 탈락률 — 분해했더니 1번이 100%다

`detect_stage2_template` 이 이제 조건별 판정을 낸다(관측 전용 · `active` 로직 무변경):

```json
캔들 140개 → {"candle_count_ok": false, "alignment_ok": null, "slope_ok": null, "high_distance_ok": null}
```

**평가 불가는 `false` 가 아니라 `null` 이다** — "틀렸다"와 "재보지 못했다"는 다른 상태다.
이 구분이 없어서 두 달간 "조건이 까다롭다"로 오해됐다.

| 조건 | 탈락률 (주식·지수 전량) |
| --- | --- |
| 1. 캔들 ≥ 200 | **100%** |
| 2. `close > ma150 > ma200` | **미평가** |
| 3. `ma200 기울기 > 0` | **미평가** |
| 4. 고점 대비 ≥ −25% | **미평가** |

D4 가 물은 것("통과율 0%가 조건이 옳아서인가 평가 불가능해서인가")의 답은 **후자**다.

---

## 2. 판정 D — 자산군 오분류 (WO 가 예상하지 못한 것)

### Bitget 은 RWA 를 명시한다. 우리는 그것을 버린다

```
Bitget USDT-FUTURES 계약 759건 중 isRwa=YES : 294건
symbol_catalog 의 asset_class            : stock 289 · index 6 · crypto 464   ← 정확하다
발견(universe_discoveries) 의 asset_class : stock 2심볼 · index 1심볼         ← 틀렸다
```

`classify_asset_class` 는 `isRwa` 를 1차 신호로 쓰도록 작성됐고 **카탈로그는 옳다.**
그런데 발견 경로가 카탈로그를 쓰지 않는다:

```python
scout/universe.py:68        asset_class = summary.get("asset_class") or analysis.get("asset_class") or item.get("asset_class")
                            #             └─ 이것이 이긴다                                            └─ 카탈로그(정확)
services/scout_handlers.py  "asset_class": analysis.get("asset_class") or classify_asset_class(symbol)
                            #                                            └─ 메타데이터 없음 → 하드코딩 허용목록
marketdata/assets.py:18     STOCK_TICKERS = { ~27개 }                    ← INTC·MRVL·DELL·TSM·RKLB·IREN 없음
```

`build_universe` 는 카탈로그의 정확한 분류를 `item` 에 넣는데, `summary` 가 심볼만으로
재분류한 값이 `or` 사슬에서 **먼저 와서 이긴다.**

허용목록은 27개, RWA 계약은 294개 → **약 262종의 토큰화 주식이 crypto 로 취급된다.**

### 같은 상품이 이름에 따라 다르게 취급된다

| 심볼 | `isRwa` | 카탈로그 | 발견에서 | `stage2` | `earnings_window` |
| --- | --- | --- | --- | --- | --- |
| AAPLUSDT | YES | stock | **stock** | 적용 | 적용 |
| INTCUSDT | YES | stock | **crypto** | **건너뜀** | **건너뜀** |
| MRVLUSDT | YES | stock | **crypto** | **건너뜀** | **건너뜀** |
| DELLUSDT | YES | stock | **crypto** | **건너뜀** | **건너뜀** |

허용목록에 AAPL 이 있고 INTC 가 없다는 이유만으로 갈린다.

### 그리고 최악의 체결 사고가 여기서 나왔다

과거 페이퍼 거래 40건의 실제 정체:

| 심볼 | `isRwa` | 카탈로그 | 거래 수 |
| --- | --- | --- | --- |
| **SPCXUSDT** | **YES** | **stock** | **2** |
| MRVLUSDT | YES | stock | 1 |
| INTCUSDT | YES | stock | 1 |
| DELLUSDT | YES | stock | 1 |
| HYPE·ETH·BTC·BASED·XRP·SOL·LINK·BNB | NO | crypto | 35 |

**주식 표본은 0건이 아니라 5건이다.** crypto 로 잘못 표시돼 있었다.

그리고 `SPCXUSDT` 는 `RISK-SIZING-01` Phase 2·4 가 특정한 **그 갭 거래**다:

```
이탈 12.5% · 초과 +2.510R · grossR −3.510
손절 초과분의 56.1%  ·  MDD 17.52 의 약 절반
```

> **검증 표본 전체에서 최악의 체결 사고는, 실적창·2단계 보호 없이 crypto 규칙으로 거래된
> 주식이었다.** `RISK-SIZING-01` 은 그것을 "갭은 수리 대상이 아니라 예산에 반영할 상수"로
> 결론냈다. 그 결론은 **원인을 잘못 짚었다** — 자산군이 옳았다면 `earnings_window` 가
> 그 진입을 걸렀을 수 있다.

`DISCOVERY-UNBLOCK-01` 로 새로 진입한 DELL·INTC 도 같은 오분류다.

---

## 3. 대응 — 순서가 중요하다

### 이 WO 에서 한 것: 계측만 (C1)

- `detect_stage2_template` 에 `candle_count` · `checks` 추가. **`active` 로직 diff 0줄**
- 평가 불가 조건을 `None` 으로 구분
- 임계값·크립토 경로·진입 게이트 9종 전부 diff 0줄

### 하지 않은 것과 그 이유

**캔들 공급 수리(3-3 A)를 적용하지 않았다.** 두 경로 모두 이 WO 범위를 넘는다:

| 방안 | 문제 |
| --- | --- |
| (a) `provider.py:689` `limit=200` → 260 | **모든 감지기의 입력 길이가 바뀐다.** `_active_fvgs` 는 `range(2, len(candles))` 를 돈다 — 캔들이 늘면 FVG 수가 늘고 와이코프·유동성 출력도 달라진다. C4(판정 로직 무변경)의 정신을 코드 diff 0줄로 위반한다 |
| (b) `stage2` 게이트에만 별도 장기 시계열 공급 | 공유 배열을 건드리지 않아 안전하지만, 스캔마다 심볼별 조회가 추가된다. `WORKER-HANG-02` 로 방금 안정화한 잡 큐에 부하를 얹는다 |

**(b) 가 옳은 방향이다.** 다만 **분류 수리가 선행되어야 한다** — 지금 stock/index 는 3심볼이라
비용이 무시할 만하지만, 분류를 고치면 **289심볼**이 되어 조회 비용이 100배가 된다.
순서를 뒤집으면 큐를 다시 굶긴다.

### 권고 순서

```
1. 자산군 분류 수리 (판정 D)     ← summary 가 카탈로그를 덮지 않게. 허용목록 의존 제거
2. 그 위에서 stage2 탈락률 재측정  ← 처음으로 **유효한 모집단**(289심볼) 위에서
3. 캔들 공급 수리 (판정 A · 방안 b) ← 비용 산정을 2번 결과로
4. 조건 2·3·4 의 실제 탈락률 산출  ← 사상 최초
```

> **1번은 표본을 줄인다.** DELL·INTC·MRVL 이 stock 으로 옮겨가 `stage2` 에 걸리고,
> 크립토 유니버스가 좁아진다. 그것이 옳다 — 지금 늘어난 표본의 일부는 **주식을 크립토로
> 착각해서 얻은 것**이다. 표본 수보다 표본의 정체가 먼저다.

---

## 4. 주식 트랙을 검증 대상에서 제외하지 않는다

판정이 C(정당한 거부)가 아니므로 제외 선언을 하지 않는다. 다만 현재 상태를 명시한다:

| 트랙 | 표면상 | 실제 |
| --- | --- | --- |
| 주식 KR (`stock_paper`) | 표본 0 | 별도 엔진. 청산 체결 14건 존재하나 트랙 판정 미도달 — **이 WO 범위 밖** |
| 주식 US (Bitget RWA) | 표본 0 | **5건 존재.** crypto 로 오표시 |

**"주식 표본 0"은 계측 오류였다.** 자산군을 고치면 표본이 나타난다 — 새로 만드는 것이 아니라
이미 있던 것을 올바르게 세는 것이다.

---

## 5. 금지

- 실측 없이 `stage2_template` 임계 변경 — 조건 2·3·4 는 아직 한 번도 측정되지 않았다
- 조건 2·3·4 의 탈락률을 추정으로 적기 — **미평가는 미평가다**
- 캔들 공급을 `provider` 공유 한도 인상으로 고치기 — 모든 감지기 출력이 바뀐다
- 분류 수리 전에 캔들 공급을 고치기 — 비용이 100배가 되고 큐가 다시 굶는다
- 표본 수 증가를 목적으로 자산군 오분류를 방치하기 — SPCX 갭이 그 대가였다
- `active` 판정 로직 변경 (C4) — 관측 필드 추가만 허용

---

## 6. 판정 A 후속 — 원인이 한 겹 더 있었다 (REPLAY-DEPTH-01 4-1)

§1 은 `provider.py:689 limit=200` → 미확정봉 제거 = 199 로 봤다. **실측하니 provider 는
모든 심볼에 정확히 200개를 준다.** 줄어드는 곳이 하나 더 있었다:

```
get_ohlcv(200) → 200 → 미확정 제거 → 199 → **세션 필터** → 146
                                              marketdata/sessions.py:100-105
```

`filter_analysis_candles` 가 **stock·index 에서만** `session=="closed"` 봉을 떨어낸다
(실측 손실률 약 30%). 그리고 `stage2_template` 이 걸리는 자산군이 정확히 그 둘이다 —
**캔들이 깎이는 쪽에 ≥200 을 요구하는 이중 구속**이었다.

그리고 §1 이 미상으로 남긴 `AAPLUSDT 140` 의 원인도 확정됐다: **상장 기간이 아니다.**
깊은 로더는 AAPL 에 2,161봉(360일)을 준다.

정본: [`CANDLE_SUPPLY.md`](CANDLE_SUPPLY.md)

---

## 7. 3-3 대응 — `earnings_window` 는 건너뛴 것이 아니라 **공급원이 없다** (WO-FCE-ASSET-CLASS-01)

§6 이 캔들 공급 쪽을 다뤘다면 이것은 게이트 쪽이다. **그리고 전제가 하나 틀렸다.**

`STOCK-UNBLOCK-01` 판정 D 는 "오분류된 262종이 `earnings_window` 도 건너뛴다"고 봤다.
관찰은 맞다. 그러나 분류를 고쳤을 때의 결과가 다르다 — 게이트가 **제대로 걸리는** 것이
아니라 **영구 차단**이 된다.

```python
paper/service.py:2262   _earnings_clear(analysis)
  crypto  → True                      # 무조건 통과
  stock   → bool({}) == False         # analysis["earnings"] 를 채우는 코드가 없다
  index   → bool({}) == False
```

실측(`audit_earnings_gate_inputs`): 실적 데이터를 주면 stock 도 통과한다. **게이트 로직은
정상이고 없는 것은 공급원이다.** 저장소 전체에서 `analysis["earnings"]` 를 읽는 곳은 그
한 줄이고 쓰는 곳은 없다.

발견 경로는 반대로 무력화돼 있다 — `scout/universe.py:85` 이 `earnings_blocked=False` 를
**하드코딩**해 자산군과 무관하게 항상 통과시킨다.

> 한쪽은 무력화, 다른 쪽은 영구 차단. **둘 다 "실적을 보고 판단한다"가 아니다.**

### 주식 트랙에 무엇을 뜻하는가

분류를 고치면 262종이 주식 트랙의 게이트를 받는다. 그 순간 `earnings_clear` 로 **진입이
0이 된다** — `FULL-AUDIT-01` 이 남긴 "주식 청산 미도달"이 해소되기는커녕 모집단이 20배
커진 채 그대로 재현된다.

**그래서 분류 수리 전에 실적 공급원 결정이 선행한다.** 선택지와 트레이드오프는
[`ASSET_CLASS.md`](ASSET_CLASS.md) §2 에 있다. 이 문서는 권고하지 않는다.

### `SPCXUSDT` 갭 — 판정 불가

`SPCXUSDT` 는 분류 변경 대상에 **포함된다**(감사 확인). 그러나 실적 캘린더 공급원이 없어
12.5% 갭이 실적 갭이었는지는 **판정할 수 없다.** 가설을 기각도 채택도 하지 않는다 —
인과 단정 금지.


---

## 8. 이중 구속 확정 — 어느 하나만 고쳐도 트랙은 안 열린다 (WO-FCE-EARNINGS-SUPPLY-01)

`FULL-AUDIT-01` 의 "주식 청산 체결 0건"은 `stage2_template` **단독** 결과가 아니었다.
게이트 둘이 동시에 막고 있었다:

| 구속 | 무엇 | 상태 |
| --- | --- | --- |
| 캔들 | `stage2_template` 200봉 — 세션 필터로 146까지 떨어짐 | `REPLAY-DEPTH-01` 4-3 대기 |
| **실적** | `earnings_clear` — 공급원이 없어 stock·index 영구 불통과 | `EARNINGS-SUPPLY-01` 진행 중 |

**올바르게 stock 으로 분류된 27종(AAPL 등)은 실적 게이트에서도 계속 차단돼 왔다.**
캔들만 고쳐도 이 게이트가 남고, 실적만 고쳐도 캔들이 남는다.

### 4-3 완료분 — 무데이터가 더는 조용한 차단이 아니다

3상태(`clear` / `earnings_window` / `not_evaluable`)를 KR 트랙에서 이식했고, 공급원 배선
전까지는 `required=False`(판정 제외)다. 이로써 주식 트랙의 **실적 쪽 구속은 일단 풀렸다** —
단 캔들 쪽은 그대로이므로 트랙이 열린 것은 아니다.

정본: [`EARNINGS_GATE.md`](EARNINGS_GATE.md).
