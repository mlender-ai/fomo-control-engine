# 자산군 분류 (Asset Class)

> WO-FCE-ASSET-CLASS-01 3-2·3-3 정본.
> 정본 코드: `backend/app/marketdata/asset_class_audit.py` (**감사 전용 — 분류를 바꾸지 않는다**)
> 실행 도구: `backend/scripts/asset_class_report.py` (읽기 전용)
> 관련: [`STOCK_TRACK.md`](STOCK_TRACK.md) · [`CANDLE_SUPPLY.md`](CANDLE_SUPPLY.md) · [`ENGINE_BUDGET.md`](ENGINE_BUDGET.md)

---

## 0. 이 문서의 상태 — 분류는 아직 고치지 않았다

**C1 이 막고 있다.** 3-1(라이브 타임아웃 종결)이 닫히기 전에는 심볼을 늘리지 않는다.
그리고 감사 과정에서 **두 번째 차단 사유**가 나왔다(§2) — 분류만 고치면 262종의 진입이
0이 된다.

이 커밋이 한 것은 **계측과 판정 근거**다:

| 작업 | 상태 |
| --- | --- |
| 3-2 분류 수리 | **미착수** — C1(3-1 미종결) + §2(실적 공급원 부재) |
| 3-2 감사 도구 | 완료 — 무엇이 몇 종 바뀌는지 읽기 전용으로 낸다 |
| 3-3 `earnings_window` | **전제 정정** — 건너뛰는 것이 아니라 고치면 영구 차단이 된다 |
| 3-4 세션 필터 영향 | 산식 확정 — 200봉을 채우려면 공급 286봉이 필요하다 |

---

## 1. 버그는 `classify_asset_class` 가 아니라 호출부에 있다 (D2)

```python
scout/universe.py:68
asset_class = str(summary.get("asset_class")      # ← 이름 기반. 먼저 평가되어 이긴다
                  or analysis.get("asset_class")  # ← 이름 기반
                  or item.get("asset_class")      # ← 카탈로그(정확). 여기까지 오지 않는다
                  or "unknown")
```

`summary` 와 `analysis` 는 둘 다 같은 함수를 **메타데이터 없이** 호출한다:

```python
positions/chart_analysis.py:73     classify_asset_class(snapshot.symbol)
services/scout_handlers.py:747     classify_asset_class(symbol)
```

메타데이터가 없으면 `isRwa` 를 볼 수 없으므로 27개짜리 `STOCK_TICKERS` 허용목록으로
떨어진다. 카탈로그(`refresh_symbol_catalog`)는 `raw_metadata` 를 넘기므로 `isRwa=YES` 를
본다.

**같은 함수인데 입력이 달라 결과가 갈린다.** 실측:

| 심볼 | 이름만 | 메타데이터 포함 |
| --- | --- | --- |
| AAPLUSDT | stock | stock |
| INTCUSDT | **crypto** | stock |
| DELLUSDT | **crypto** | stock |
| MRVLUSDT | **crypto** | stock |

> 그래서 수리 방향은 "함수를 고친다"가 아니라 **"호출부가 카탈로그를 보게 한다"** 이다.
> 허용목록 유지·확대는 같은 결함을 규모만 키운다 — 294개 RWA 를 27개 목록으로 덮을 수 없다.

---

## 2. ⚠️ 분류만 고치면 262종의 진입이 0이 된다 (D3 전제 정정)

WO 는 "오분류된 262종이 `earnings_window` 도 건너뛴다"고 봤다. **그 관찰은 맞다.**
그러나 고쳤을 때 무슨 일이 일어나는지가 다르다.

`crypto → stock` 으로 옮겨가면 게이트 두 개를 새로 받는다:

| 게이트 | 위치 | 지금(crypto) | 수리 후(stock) |
| --- | --- | --- | --- |
| `stage2_template` | `scout/universe.py:239` | 건너뜀 | 적용 |
| `earnings_clear` | `paper/policy.py:173` | 항상 통과 | **항상 불통과** |

### 왜 "적용"이 아니라 "영구 차단"인가

```python
paper/service.py:2262
def _earnings_clear(analysis):
    if str(analysis.get("asset_class")) not in {"stock", "index"}:
        return True                                   # crypto → 무조건 통과
    earnings = _dict(analysis.get("earnings") or analysis.get("earnings_risk"))
    return bool(earnings) and ...                     # {} → False
```

**`analysis["earnings"]` 를 채우는 코드가 크립토 분석 경로에 없다.** 저장소 전체에서 그 키를
읽는 곳은 위 한 줄뿐이고, 쓰는 곳은 없다. 실측:

| 자산군 | 실적 데이터 없음 | 실적 데이터 있음(창 밖) | 영구 차단 |
| --- | --- | --- | --- |
| crypto | 통과 | 통과 | 아니오 |
| stock | **불통과** | 통과 | **예** |
| index | **불통과** | 통과 | **예** |

게이트 **로직**은 정상이다 — 데이터를 주면 통과한다. 없는 것은 **공급원**이다.

> `DISCOVERY-UNBLOCK-01` 이 유니버스를 3→15 로 늘려 예산을 터뜨렸을 때의 교훈이
> "표본을 늘리려는 변경이 표본을 0으로 만들었다"였다. 분류 수리를 지금 하면 **같은 문장이
> 다시 쓰인다** — 이번엔 262종 규모로.

### 발견 경로 쪽은 반대로 무력화돼 있다

```python
scout/universe.py:85     earnings_blocked=False      # ← 하드코딩
```

발견 게이트의 `earnings_window` 는 인자가 상수라 **자산군과 무관하게 항상 통과**한다.
즉 실적 게이트는 한쪽에서는 무력화, 다른 쪽에서는 영구 차단이다. **둘 다 "실적을 보고
판단한다"가 아니다.**

### 결정이 필요하다 (사용자)

분류 수리 전에 셋 중 하나를 정해야 한다:

1. **실적 캘린더 공급원을 배선한다** — 게이트가 설계대로 동작한다. 범위가 가장 크다
2. **무데이터 = 통과로 정의한다** — 지금 crypto 와 같은 취급. 게이트가 사실상 없는 상태가
   명시적으로 기록된다. `earnings_clear` 임계 변경이므로 C2 저촉 여부 판단이 필요하다
3. **분류 수리를 보류한다** — 현행 유지. 262종이 계속 crypto 로 취급된다

**이 문서는 권고하지 않는다.** 셋 다 트레이드오프가 있고, 2번은 게이트 완화이므로
C7("진입 감소를 이유로 게이트 완화") 의 반대 방향 위반 가능성이 있다.

---

## 3. 세션 필터 영향 (3-4)

`crypto → stock` 이동 심볼은 `filter_analysis_candles` 를 새로 받는다. 실측 손실률은
약 30%(`CANDLE_SUPPLY.md` §0)이므로:

```
200 ÷ (1 − 0.30) = 286봉
```

**공급이 286봉을 넘겨야 `stage2_template` 이 200봉 요건을 채운다.** 최근봉 경로(200)로는
불가능하고, 깊은 로더(2,196봉)를 쓰면 여유가 크다 — `REPLAY-DEPTH-01` 4-2 의 히스토리
백필이 그 공급원이다.

| 심볼 | 깊은 로더 | 세션 필터 후 | 200 요건 |
| --- | ---: | ---: | --- |
| AAPLUSDT | 2,161 | 1,507 | 충족 (7.5배) |
| INTCUSDT | 2,083 | 1,453 | 충족 (7.3배) |
| SPCXUSDT | 439 | ~307 | 충족 |
| BASEDUSDT | 865 | ~605 | 충족 |

> 신규 상장 심볼도 세션 필터 후 200봉을 넘는다. **3-4 의 답은 "캔들은 병목이 아니다"** 이며,
> `REPLAY-DEPTH-01` 4-3 의 실제 선행 조건은 캔들이 아니라 **§2 의 실적 결정**이다.

---

## 4. 수리 설계 (미적용 — 3-1 종결 + §2 결정 후)

정하고 가는 것:

| 항목 | 방향 |
| --- | --- |
| 정본 | `symbol_catalog` — `raw_metadata.isRwa` 기반. 이름 기반 재분류 제거 |
| 적용 지점 | `chart_analysis` · `_summary_row` 가 카탈로그 분류를 받도록 **호출부**를 고친다 |
| 원복 | 설정 한 값(C4). 기본값 현행 |
| 단계 확대 | 3-1 이 낸 예산 한계 안에서. 거래량 상위부터. 전량 289종 일괄 금지 |
| 사후 채점 | 분류 변경 심볼의 성적 분리 집계(C5). `universe_source` 선례 |

**허용목록(`STOCK_TICKERS`) 폐기가 최종 방향**이지만, 카탈로그가 비었을 때의 대체 경로가
필요하므로 폐기는 카탈로그 신뢰도 확인 후로 미룬다.

---

## 5. 실행

```bash
cd backend
PYTHONPATH=. python3 scripts/asset_class_report.py --database ~/fomo_control_engine.db
```

카탈로그가 비어 있으면 **추정하지 않고 보고하고 종료**한다 — `refresh_symbol_catalog`
잡이 먼저 돌아야 한다.

---

## 6. 아직 산출하지 않은 것 (정직성)

이 컨테이너에는 운영 DB 도 거래소 네트워크도 없다. 아래는 **호스트에서 §5 를 실행해야**
나온다 — 추정으로 채우지 않았다:

| 항목 | 산출 방법 |
| --- | --- |
| 실제 분류 변경 심볼 수·목록 | `scripts/asset_class_report.py` §1·§4 |
| 카탈로그 RWA 실측 (294 추정) | 같은 스크립트 |
| `earnings_clear` 를 건너뛴 과거 진입 목록 (3-3 작업 1·2) | 운영 `paper_trades` 필요 |
| `SPCXUSDT` 갭의 실적 관련성 (3-3 작업 3) | 실적 캘린더 필요 — **공급원이 없어 판정 불가** |

> `SPCXUSDT` 는 분류 변경 대상에 **포함된다**(감사에서 확인). 그러나 실적 캘린더가 없어
> 12.5% 갭이 실적 갭이었는지는 **판정 불가**다. 가설을 기각도 채택도 하지 않는다 —
> 인과 단정 금지(3-3).

---

## 7. 금지

- 3-1 종결 전 분류 수리 적용 (C1) — 이 WO 의 핵심 제약
- §2 결정 전 분류 수리 적용 — 262종의 진입이 0이 된다
- 게이트 임계 변경 (C2) — 적용 대상만 바꾼다
- 전량 289종 일괄 적용 — 단계 확대
- 허용목록 확대로 때우기 — 294개 RWA 를 27개 목록으로 덮을 수 없다
- `SPCXUSDT` 갭에 대한 인과 단정
- 진입 감소를 이유로 게이트 완화 (C7)
