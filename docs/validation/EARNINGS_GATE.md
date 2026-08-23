# 실적 게이트 (Earnings Gate)

> WO-FCE-EARNINGS-SUPPLY-01 정본. **개정 §2 반영** — 무데이터는 차단이 아니라 `not_evaluable`.
> 정본 코드: `backend/app/paper/earnings_state.py`
> 관련: [`ASSET_CLASS.md`](ASSET_CLASS.md) · [`STOCK_TRACK.md`](STOCK_TRACK.md) · [`ENGINE_BUDGET.md`](ENGINE_BUDGET.md)

---

## 0. 상태 — 4-3만 완료됐다

| 작업 | 상태 |
| --- | --- |
| 4-1 공급원 조사 | **미착수** — 외부 네트워크 필요 |
| 4-2 캘린더 배선 | **미착수** — 4-1 선행 |
| **4-3 3상태 이식** | **완료** (이 문서) |
| 4-4 발견 경로 하드코딩 제거 | **미착수** — 4-2 선행 |
| 4-5 소급 검증 | **미착수** — 운영 DB 필요 |
| 4-6 호스트 실측 | **미착수** — 운영 호스트 필요 |

4-3은 공급원과 무관하게 성립하는 유일한 작업이라 먼저 했다. **진입 판정을 바꾸지 않는
표현 변경**이며(크립토 기준), 공급원이 붙었을 때 켤 스위치를 함께 넣었다.

---

## 1. 문제 — 불리언 하나가 두 가지를 뭉갠다

```python
paper/service.py:2262   _earnings_clear(analysis) -> bool
  crypto  → True
  stock   → bool({}) == False    # 데이터가 없어서
  index   → bool({}) == False    # 실적 구간이라서 — **와 구분되지 않는다**
```

`analysis["earnings"]` 를 **읽는 곳은 이 한 줄이고 쓰는 곳은 저장소 전체에 0건**이다.
따라서 stock·index 는 항상 `False` 였다.

> **모르는 것과 아니라고 판정한 것을 같은 값으로 적으면, 공급 결함이 게이트 성과로
> 위장된다.** "실적 게이트가 잘 걸러서 진입이 없다"와 "데이터가 없어서 전부 막혔다"는
> 처방이 정반대다.

발견 경로는 반대로 망가져 있다 — `scout/universe.py:85` 가 `earnings_blocked=False` 를
하드코딩해 자산군과 무관하게 항상 통과시킨다. **한쪽은 영구 차단, 다른 쪽은 무력화.**
(하드코딩 제거는 4-4 소관 — 캘린더가 붙은 뒤다.)

---

## 2. KR 트랙은 이미 풀었다 — 재사용한다 (불변 규칙 2)

```python
stock_paper/policy.py:66
results["earnings_gate"] = {
    "status": "not_evaluable",     # ← 통과도 차단도 아닌 제3상태
    "threshold": "source_backlog",
    "required": False,             # ← 판정에서 제외
}

stock_paper/parameters.py:56       # 불변식이 재방문을 강제한다
if version in {"stock-v2","v3","v4"} and earnings_gate_mode != "not_evaluable":
    raise ValueError("earnings must remain explicitly not_evaluable until a source is connected")
```

KR 트랙은 (a) 제3상태를 명시하고 (b) 불변식으로 "공급원이 붙을 때까지"라고 코드가 스스로
말하게 했다. **설계가 이미 있으므로 새로 만들지 않고 어휘와 구조를 크립토로 옮겼다.**

`stock_paper/` 는 이 WO 에서 **한 줄도 바꾸지 않았다**(개정 §1-5).

---

## 3. 3상태

| 상태 | 뜻 | `required=False` | `required=True` |
| --- | --- | --- | --- |
| `clear` | 실적 창 밖 · 또는 게이트 대상 아님(crypto) | 통과 | 통과 |
| `earnings_window` | 데이터가 있고 **D-1~D+1** | **차단** | **차단** |
| `not_evaluable` | 공급원이 없어 **재보지 못함** | 통과 | **차단** |

**`earnings_window` 는 두 모드 모두 차단이다.** 이 WO 는 실적 구간 차단을 완화하지 않는다.

### 판정은 재구현하지 않았다 (C1)

`days_to_event not in {-1, 0, 1}` 같은 임계는 새 모듈에 **없다.** `earnings_state()` 는
`_earnings_clear` 를 **주입받아 호출**하고, 그 앞에 "페이로드가 비었는가" 하나만 더 본다.
그것이 이 WO 가 추가한 유일한 술어다.

회귀가 이 사실을 AST 로 강제한다
(`test_earnings_state.py::test_module_does_not_redefine_the_threshold`).

---

## 4. `required` 는 왜 기본값이 False 인가 (개정 §2)

원안은 "무데이터 = 차단 유지"였다. **철회됐다.**

지금 차단으로 두면 **얻는 것 없이 관측만 잃는다**:

- 오분류된 262종은 현재 `crypto` 라 이 게이트를 받지 않는다 — 차단해도 영향 0
- 올바르게 `stock` 인 소수만 추가로 죽는데, 그 심볼들은 `stage2_template`(캔들 200봉)에서
  **이미 막혀 표본이 0**이다

그리고 KR 트랙의 `parameters.py:56` 불변식과 정면으로 충돌한다 — 두 엔진이 같은 상황을
정반대로 처리하게 된다.

```
FCE_PAPER_EARNINGS_GATE_REQUIRED=false    ← 기본값. KR 트랙과 같은 취급
FCE_PAPER_EARNINGS_GATE_REQUIRED=true     ← 공급원 배선 후 전환
```

### 전환 순서가 중요하다 ★

```
공급원 배선 (4-1·4-2)
   ↓  커버리지 확보 확인
required=True 전환
   ↓
ASSET-CLASS-01 3-2 분류 수리      ← 여기서 262종이 stock 이 된다
```

**역순으로 하면 262종이 무방비로 거래되거나(required=False) 전멸한다(required=True).**
분류 수리 시점에는 실적 게이트가 이미 작동 중이어야 한다.

---

## 5. 진입 건수 영향 — 정직하게

| 자산군 | 현행 | `required=False` | 변화 |
| --- | --- | --- | --- |
| crypto | 통과 | 통과 | **없음** |
| stock (무데이터) | 차단 | 통과 | **있음** |
| index (무데이터) | 차단 | 통과 | **있음** |

**크립토는 정확히 0의 변화**다. 현행 라이브 유니버스는 사실상 전부 crypto(262종 오분류
포함)이므로 실질 진입 건수는 그대로다 — 개정 §2 가 "진입 건수 전후 동일"이라 적은 근거가
이것이다.

**그러나 stock·index 에서는 동작이 바뀐다. 숨기지 않는다.** 무데이터 심볼이 이 게이트에서
더는 막히지 않는다. 실제 진입이 늘어나는지는 나머지 게이트에 달려 있고, 그 심볼들은
`stage2_template` 에서 이미 막혀 있다 — 하지만 그것은 **관측으로 확인할 사실이지 보장이
아니다.** 호스트 실측(4-6)에서 확인해야 한다.

회귀가 두 사실을 각각 고정한다:
`test_crypto_entry_decision_is_unchanged_in_both_modes` ·
`test_stock_without_a_feed_stops_being_blocked_when_not_required`.

---

## 6. 관측 (C9 — 침묵 금지)

거부 퍼널 레코드에 `earnings_gate` 관측치가 붙는다. 모양은 KR 트랙과 같다:

```json
{"status": "not_evaluable", "measured_value": "not_evaluable",
 "threshold": "source_backlog", "required": false, "passed": true}
```

`coverage_summary()` 가 무데이터 비율을 낸다 — **이 값이 높으면 그것 자체가 결함이다.**
표본 0에서는 비율을 만들어 내지 않고 `None` 을 낸다.

---

## 7. 아직 산출하지 않은 것

| 항목 | 필요한 것 |
| --- | --- |
| 공급원 후보·커버리지 (4-1) | 외부 네트워크 |
| 캘린더 배선 (4-2) | 4-1 결정 |
| 발견 경로 하드코딩 제거 (4-4) | 4-2 선행 |
| `earnings_clear` 건너뛴 과거 진입 (4-5) | 운영 `paper_trades` |
| `SPCXUSDT` 갭 실적 관련성 (4-5) | 실적 캘린더 — **현재 판정 불가** |
| 무데이터 실제 비율 (4-6) | 운영 호스트 |

**추정으로 채우지 않았다.**

---

## 8. 금지

- 무데이터를 **조용한** 통과나 **조용한** 차단으로 두기 (개정 C2) — `not_evaluable` 로 명시한다
- `_earnings_clear` 판정 로직·임계 변경 (C1)
- `earnings_window` 차단 완화 — 데이터가 있을 때의 판정은 건드리지 않는다
- `stock_paper/parameters.py` 불변식 수정 (§1-5) — 공급원이 KR 까지 덮으면 별건으로
- 커버리지 확인 없이 `required=True` 전환
- **분류 수리(ASSET-CLASS-01 3-2)를 `required=True` 전환보다 먼저 하기** — 순서가 안전장치다
- 평가 경로에 동기 네트워크 호출 추가 (C6) — 호가 관측 사고 재발
- 추정 실적일 생성 (C8)
