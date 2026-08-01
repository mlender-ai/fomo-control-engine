# 포지션 구조 컨텍스트 (와이코프 · 오더블록)

WO-FCE-STRUCTURE-CONTEXT-01 정본.

> **구조는 관측이며 예측이 아니다.**
> 이 기능은 "축적 국면이므로 오른다" 같은 인과를 주장하지 않는다. 내 진입가가 어느 레인지·존
> 안에 있는지를 **서술**할 뿐이다. PNF 측정 목표도 "측정된 목표치"이지 도달 보장이 아니다.

## 무엇이 없었나 — 재료는 다 있고 연결이 없었다

| 재료 | 위치 | 상태 |
|---|---|---|
| 와이코프 국면·레인지·이벤트 | `structure/wyckoff/engine.py::analyze_wyckoff` | 이미 있었음 |
| 오더블록 리테스트 | `structure/candidates/engine.py::_order_block_retest` | 이미 있었음 |
| PNF 측정 목표 | `structure/pnf` | WO-PNF-TARGET-01에서 추가 |

없던 것은 **연결**이다. 시스템은 "내 진입가가 축적 레인지 안인지, 수요 오더블록 위인지"를 알고 있었지만 사용자에게 보여주지 않았다. 알림도 "이벤트가 났다"만 보냈지 "내 포지션과 어떤 관계인가"가 없었다.

**신규 감지기는 추가하지 않았다**(모라토리엄 유지). `order_block_zones()`는 `_order_block_retest`가 이미 쓰던 존 도출 로직(구조 파괴 → 직전 반대 캔들 실체)을 그대로 쓰되 "지금 터치했는가"라는 발화 조건만 떼어낸 것이다. 리테스트 이벤트는 발화 순간만 알려주므로 "내 포지션이 어느 존 안인가"를 답할 수 없었다.

## 산출 항목

`structure/context.py::build_structure_context`

### 와이코프
`phase`(축적/분산/마크업/마크다운), `range_low`/`range_high`, `range_width_pct`, `candles_inside`, `recent_markers`(SC·ST·Spring·LPS·SOS + 발생 시각), `accumulation_score`/`distribution_score`, `conflict_note`.

### 오더블록
`zones`(가격 범위·형성 시각·리테스트 횟수·미시험 여부), `entry_zone`(진입가와 겹치는 존), `nearest_demand`/`nearest_supply`.

### 포지션 관계 (핵심)

| 필드 | 의미 |
|---|---|
| `entry_range_position` | 진입가가 레인지 `inside`/`above`/`below`/`unknown` |
| `mark_range_position` | 현재가 기준 동일 판정 (전이 감지에 사용) |
| `entry_in_order_block` | 진입가가 OB 존과 겹치는가 |
| `invalidation_vs_range_low` | 무효화선이 레인지 하단 대비 `above`/`below` |
| `invalidation_vs_entry_zone_low` | 무효화선이 진입 OB 하단 대비 위치 |
| `pnf_target_price` / `pnf_remaining_pct` | 측정 목표와 현재가 대비 잔여 거리 |

경계 판정에는 레인지 폭의 0.1% 허용 오차를 둔다 — 경계에 걸친 가격을 '밖'으로 단정하지 않기 위함이다.

### 판정 1줄

```
진입 1710.89 · 축적 레인지(1680~1750) 내부 · 수요 OB(1695~1712) 겹침 · 무효화 1674는 레인지 하단 아래.
```

**서술만 담는다.** 회귀 가드가 `따라서`·`이므로`·`상승할`·`하락할`·`예상`·`전망` 문구를 금지한다.

## 리페인팅 표기 (숨기지 않는다)

와이코프 국면은 **본질적으로 사후 재해석된다.** 이전 관측과 국면이 달라지면 숨기지 않고 드러낸다:

```json
"repaint": {"repainted": true, "previous_phase": "accumulation", "note": "국면 재해석됨 — 이전 판정과 상이합니다."}
```

판정 1줄에도 `국면 재해석됨`이 붙는다. 재해석 자체는 결함이 아니지만, **재해석됐다는 사실을 감추는 것**은 결함이다.

## 알림 — `position_structure_event`

**보유 포지션이 있을 때만 발화한다.** 시장 전체 와이코프 이벤트는 기존 `wyckoff_event`가 담당하며 이 WO에서 변경하지 않았다.

### 발화 조건 (전이 시에만)

| kind | 조건 |
|---|---|
| `range_exit` / `range_enter` | 현재가의 레인지 내외 위치가 바뀜 |
| `order_block_enter` / `order_block_exit` | 진입 관련 OB 존 진입·이탈 |
| `phase_change` | 축적→마크업 등 국면 전이 |
| `spring_or_lps` | 보유 종목에서 Spring/LPS 마커 신규 확정 |
| `pnf_target_reached` | 측정 목표 상향 교차 |

### 스팸 방지 (C6)

- `detect_structure_transitions(previous, current)`는 **전이만** 반환한다. 같은 상태가 100틱 반복돼도 0건이다(회귀 테스트로 강제).
- **최초 관측은 전이가 아니다** — 첫 틱에 알림이 쏟아지지 않는다.
- 상태 키는 `{종목}:{이벤트유형}:{레인지ID}`이며 기존 쿨다운·중복 억제 계층을 함께 탄다.
- 직전 컨텍스트는 `NotificationState.structure_contexts`에 영속화되어 재기동에도 유지된다.

### 화이트리스트 (C5)

`RULE_LABELS`·`RULE_SEVERITY`·`alert_rules_enabled` 기본값에 `position_structure_event`를 명시 등록했다. 거부 알림은 추가하지 않았다 — 진입 중심 알림 원칙(WO-ENTRY-CENTRIC-ALERTS-01) 유지.

### 메시지 형식

```
🏗 구조 이벤트 · ETHUSDT (롱 보유)
· 레인지 이탈 (경계 1680)
· 내 진입 1710.89 · 무효화 1674
· 최근 마커: Spring
관측 정보이며 매매 신호가 아닙니다.
```

마지막 줄은 고정이다.

## 페이퍼 반영 — 1·2단계만 (게이트 편입 아님)

**게이트에 넣지 않는다**(C2). 표본이 없는 상태에서 진입 조건을 바꾸는 것은 개선이 아니라 추측이다.

- **1단계(기록)**: 진입·청산 스냅샷에 구조 컨텍스트를 기록한다. 목적은 "축적 레인지 내부 진입이 밖 진입보다 성적이 좋은가"를 나중에 실측할 데이터를 **지금부터 쌓는 것**이다.
- **2단계(candidate 등록)**: `structure_context/range_inside_entry`, `structure_context/ob_demand_entry`, `structure_context/ob_supply_entry`를 candidate 시그니처로 등록해 원장 채점 대상에 포함한다. 승격 기준은 기존과 동일(N≥30·CI 하한·거부권)이며 **승격 전까지 진입 판단에 영향이 없다.**
- **3단계(게이트 편입)**: 이번 WO 범위 밖. validated 승격 후 별도 WO로 검토하며 **한 번에 하나씩 검증**한다(AGENTS.md).

## 룩어헤드 금지 (C3)

구조 판정은 확정 캔들만 사용한다. `order_block_zones`는 넘어온 캔들 순서를 그대로 신뢰하며 결정론적이다(회귀 테스트로 강제). 와이코프 엔진의 확정 캔들 정책은 기존 그대로다.

## 남은 것 (후속)

차트 레이어 칩(`와이코프 레인지`·`오더블록` 시각화)은 대시보드 작업이라 별도로 남긴다. 백엔드가 `order_block_zones`·`structure.context`를 이미 노출하므로 프론트는 그 값을 그리기만 하면 된다.
