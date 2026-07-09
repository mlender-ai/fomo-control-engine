# Directional Engine (방향 판정 파이프라인)

WO-FCE-50 마스터. 방향 판정은 "포지션 진입 전, 지금 롱이 우세한가 숏이 우세한가"를 구조적으로 답하는 엔진이다. 구현은 `backend/app/analyst/confluence.py`의 `build_confluence`(순수 함수)이며, 이 문서는 방향 산출 전체 파이프라인의 단일 출처다.

## 설계 철학 (상위 규범)
1. **방향 = 지금 무엇이 우세한가.** 과거는 감쇠하되 삭제하지 않는다. 구조 레벨의 역사성(존재·관측)은 유지하고, **방향 기여만** 시간 감쇠한다.
2. **상위 추세가 기준선, 하위 신호가 편차.** 하위 신호는 HTF 맥락 안에서 해석한다.
3. **전환에는 관성.** 방향은 순간이 아니라 지속으로 바뀐다. 문턱을 넘는 우세가 지속될 때만 flip, 그 전엔 "전환 관찰 중".
4. **모르면 모른다.** 진짜 구조적 균형만 `conflicted`.
5. **관측 사실 출력은 불변.** 볼륨·유동성·레벨의 1줄 판정 문구는 재설계 전후 동일. 시간가중·HTF 맥락은 방향 배정 로직에만 적용한다.

## 파이프라인 개요
```
analysis (기존 엔진 출력, 재계산 없음)
  → _htf_context()           상위 TF 추세 기준선 도출 (bias·strength·alignment, WO-52)
  → _collect_evidence()      엔진별 증거를 long/short/neutral로 정규화 (mtf 앵커 포함)
  → _apply_weight()          score = base_weight × confidence/100 × calibration_factor × recency_factor × htf_factor
                             ├─ calibration_factor : 적중률 CI 하한 기반 (WO-36, 불가침)
                             ├─ recency_factor      : 시간 감쇠 (WO-51)
                             └─ htf_factor          : HTF 기준선 배수 (WO-52)
  → _suppress_overlaps()     동근원 증거 이중 가점 차단 (WO-36)
  → _stance()                long/short 점수 비율 + HTF 가드 → raw_stance (순간)
  → _resolve_stance_state()  직전 상태 대비 히스테리시스 → stance (유지 방향) + stance_state (WO-53)
  → composite / counter_evidence / htf_context / stance_state
```

**상태 흐름 (WO-53):** `build_confluence`는 순수 함수 — `prior_state`를 주입받아 `stance_state`(새 상태)를 반환할 뿐 I/O 없음. 라이브는 `load_directional_prior(repo, symbol, tf)`가 최근 스카우트 스냅샷의 `confluence.stance_state`를 읽어 주입하고, 새 상태는 스냅샷에 브리핑이 실려 자연 영속(별도 테이블 없음). 백테스트는 인메모리로 스레딩. **전환 로직은 `_resolve_stance_state` 한 곳에만 있어 이중화가 없다.**

## WO-51 · 증거 시간 가중 (recency)

방향 기여는 "마지막으로 발생/터치한 시각" 기준으로 감쇠한다. 3주 전 저항 터치가 방금 반등과 동일 무게로 숏을 카운트하던 결함을 제거한다.

```
recency_factor = floor + (1 - floor) × 0.5 ^ (age_bars / half_life_bars)
age_bars       = (now - evidence.as_of) / bar_minutes(timeframe)
```

- **반감기는 절대 시간이 아니라 타임프레임 상대값(캔들 수).** 4h·15m 어디서도 동일 규범이 성립한다. `bar_minutes`는 `TIMEFRAME_MINUTES` 맵(4h=240) 참조.
- `floor > 0`이므로 오래된 증거도 **존재는 유지**(방향 기여만 줄어듦). state류만 사실상 0으로 소멸.

### 유형별 반감기·floor (`DECAY_PROFILES`)

| 클래스 | 반감기(캔들) | floor | 대상 증거 | 근거 시각 |
|---|---:|---:|---|---|
| `event` | 6 | 0.05 | 유동성 스윕·와이코프 마커·BOS/CHoCH·하모닉 PRZ | 발생 캔들 시각 |
| `structure` | 30 | 0.35 | 지지/저항 레벨·POC·와이코프 국면·(임시)MTF | 레벨 **마지막 터치** 시각 |
| `state` | 3 | 0.0 | 펀딩·OI 등 현재 상태값 | 신호 as_of |

4h 차트 환산: event 반감기 24h, structure 5일, state 12h.

### 마지막 터치 기준
레벨 증거의 `as_of`는 "레벨 생성 시각"이 아니라 `last_touch_at`(마지막 터치 캔들)이다. 오래 안 건드린 레벨은 방향 기여가 감소하되 목록에서 사라지지 않는다.

### stale 처리
`is_stale`(120분 하드 boolean)은 **UI 표기용으로만** 유지한다. 방향 기여는 하드 컷이 아니라 연속 감쇠(`recency_factor`)로 대체된다.

### 시각 파싱
`as_of`는 datetime / ISO 문자열 / **epoch 정수(초·밀리초)** 모두 허용한다. 종전엔 epoch 정수가 `_iso`에서 `None`으로 버려져 와이코프 마커·하모닉의 as_of가 소실되고 `is_stale`이 늘 False였다 — WO-51에서 정정.

### 스케일 불변 (회귀 안전)
감쇠로 전체 score 스케일은 낮아지지만, 방향 판정 임계는 모두 **비율 기반**이라 스케일 불변이다.
- `_stance`: `abs(long − short) / max(long, short) < CONFLICT_RATIO` — 비율.
- `composite_score`: `stronger / total × 100` — 비율.
- 방향 필터·정렬·overlap 억제: 상대 비교.
- `CONFLICT_RATIO`·`MIN_DIRECTIONAL_EVIDENCE`는 각각 비율·개수이며 절대 score 임계가 아니다.

검증: `tests/test_analyst_briefing.py::test_gate_thresholds_scale_invariant_under_uniform_decay` — 동일 클래스·동일 나이 증거는 recency가 균일하게 곱해져 stance·composite가 불변.

## WO-52 · HTF 추세 앵커 승격

HTF(상위 TF, 기본 1D)는 더 이상 60점 동급 투표자 1명이 아니라 방향 산출의 **기준선(baseline)**이다. 하위(분석 TF) 증거는 이 기준선에 대한 **편차**로 해석된다.

### 기준선 도출 (`_htf_context`)
`wyckoff_mtf`의 `htf_phase`·`htf_trend`·`alignment`에서 방향 성향과 명확도를 계산한다(원천에 수치 strength가 없어 라벨에서 도출).
```
bias_score = 0.6 × trend_score + 0.4 × phase_score          # [-1, 1]
  trend_score : bullish +1.0 / neutral_to_bullish +0.5 / neutral·undetermined 0 / bearish_to_neutral -0.5 / bearish -1.0
  phase_score : "accumulation" 포함 +1.0 / "distribution" 포함 -1.0 / else 0
bias     = long (score ≥ 0.25) / short (≤ -0.25) / neutral
strength = min(1, |bias_score|)
```

### 앵커 배수 (`htf_factor`, `_apply_weight`에 곱)
```
정렬 증거(direction == bias) : ×(1 + 0.25 × strength)   최대 ×1.25
역행 증거(direction ≠ bias)  : ×(1 − 0.45 × strength)   최소 ×0.55 (floor, 절대 0 아님)
```
- **역행 증거를 0으로 죽이지 않는다** — 감쇠만. 진짜 전환의 초기 신호를 보존하기 위함.
- 배수는 **strength에 비례** — HTF가 횡보/전환이면 배수≈1이 되어 하위 증거 비중이 자연 회복(HTF 불명확 시 과의존 방지).
- **mtf 앵커 자신은 배수 제외**(순환 방지). 앵커 증거의 신뢰도는 `50 + 40×strength`로 명확도에 비례.
- **`alignment == "conflicting"`이면 배수를 적용하지 않는다.** 앵커 배수는 "하위가 HTF 추세 내 되돌림"이라는 전제 위에서만 성립 — 정면 충돌은 되돌림이 아니라 전환/충돌이므로 아래 가드로 처리.

### 방향 가드 (`_stance`)
```
margin = |long − short| / max(long, short)
margin < CONFLICT_RATIO(0.25)                          → conflicted (기존)
HTF 반대로 기울거나(strength ≥ 0.40) alignment=conflicting
  이면서 margin < HTF_STRONG_MARGIN(0.50)              → conflicted (전환 확정은 WO-53)
그 외                                                   → long/short_leaning
```
즉 **HTF를 거스르는 clean 방향 판정은 압도적 우세(≥50%)일 때만** 허용된다. 그 미만은 conflicted로 보수화하고, 진짜 전환 flip은 지속 확인(WO-53)에 맡긴다.

### HTF 부재/횡보 시 신뢰도 하향
방향성 HTF 기준선이 없으면(`bias == "neutral"`: 데이터 부재 또는 횡보/전환) 하위 증거만으로 강한 방향을 확정하지 않는다 → `composite_score`를 `HTF_ABSENT_COMPOSITE_CAP(75)`로 상한.

### 출력 `htf_context`
```json
{ "bias": "long", "strength": 1.0, "alignment": "aligned",
  "htf_phase": "accumulation", "htf_trend": "bullish", "available": true }
```
브리핑/스카우트가 "상위 상승 추세 내 되돌림 구간" 류 맥락 문장을 생성할 수 있게 데이터만 제공(표면은 후속 WO).

### SOXL 재현 (검증)
HTF 상승(accumulation/bullish, aligned) + 하위 잔존 short(저항·고점스윕·하모닉):
| | 롱 | 숏 | stance |
|---|---:|---:|---|
| HTF 앵커 적용 | 40.5 | 13.4 | **long_leaning** (숏 전부 ×0.55 감쇠, 그러나 score>0) |
| HTF 제거(대조) | 25.2 | 24.4 | conflicted (하위만으로 강한 확정 안 함) |

`tests/test_analyst_briefing.py::test_htf_uptrend_prevents_lone_short_residue_from_shorting` 외 3건.

## WO-53 · 방향 히스테리시스 (전환 관성)

`_stance()`의 순간 판정(`raw_stance`)을 직전 상태와 비교해 **전환에 관성**을 준다. 균형 구간의 지표 1칸 깜빡임에 분단위 반전하던 결함(실증: 20:40↔20:44 4분 반전)을 제거한다. 출력 `stance`는 이제 "유지 중인(held)" 방향, `raw_stance`는 순간 스냅샷이다.

### 3중 방어 (`_resolve_stance_state`)
1. **EMA 평활** — long/short 점수를 지수이동평균(span 2)으로. 순간 스파이크 흡수.
2. **Schmitt 트리거** — enter(0.25) > exit(0.10) 비대칭 문턱으로 [0.10, 0.25] 데드존 형성. 방향을 켜기도 끄기도 어렵게 → 경계 진동 흡수.
3. **연속 확인(persist)** — 상태 변경 후보가 `flip_persist(2)`회 연속 확인돼야 채택. **주 진동 방어** — 1바 스파이크는 persist 미달로 흡수.

`rel = (long_ema − short_ema) / max(long_ema, short_ema)` (부호+=롱). held 기준 후보 판정:
| 현재(held) | → short_leaning | → conflicted | → long_leaning |
|---|---|---|---|
| long_leaning | rel ≤ −flip_margin | rel < exit | (유지) |
| short_leaning | (유지) | rel > −exit | rel ≥ flip_margin |
| conflicted | rel ≤ −enter | (유지) | rel ≥ enter |

후보 ≠ held면 persist 카운트 누적, `≥ flip_persist`에서 채택. 미달이면 held 유지 + `transitioning=True`.

### 둔감/민감 트레이드오프 (초기값 근거)
- **주 방어를 persist에 둔다** — EMA span·margin은 보조. 그래서 span을 짧게(2) 유지해 반응성을 살린다.
- **진동**: 1바 반대 스파이크 → persist 미달(count 1) → held 유지. 매 바 교대해도 flip 0.
- **진짜 전환**: 반대 우세가 지속되면 2~3바 내 flip. **추세를 놓치지 않는다**(WO-53 금지 규정).
- 초기값 `ema_span 2 · flip_margin 0.30 · enter 0.25 · exit 0.10 · persist 2`. 백테스트로 재튜닝 가능(아래).

### insufficient·bootstrap
- `raw_stance == insufficient`(증거 부족): 방향 게이트가 이긴다 — 히스테리시스로 방향을 붙들지 않고 즉시 insufficient.
- 직전 상태 없음(bootstrap): 관성 없이 `raw_stance` 즉시 채택.

### 상태 노출 (`stance_state`)
```json
{ "stance": "long_leaning", "previous_stance": "long_leaning", "transitioning": true,
  "target": "short_leaning", "flip_threshold_progress": 0.5, "candles_in_state": 3,
  "since": "...", "last_flip_at": null, "flipped": false,
  "long_score_ema": 24.1, "short_score_ema": 18.7, "pending_stance": "short_leaning", "pending_count": 1 }
```
알림·UI가 "하방 유지 (3캔들째) · 상방 전환 관찰 중 (문턱 50%)"를 표현 가능(표면은 후속 WO, 여기선 데이터).

### 알림 연동 (stance_flipped)
`oneliner._overall_stance`는 confluence가 있으면 `confluence.stance`(held)에서 직접 파생된다. 따라서 held가 **확정 flip일 때만** overall_stance가 바뀌고, `lifecycle.py`의 `stance_flipped` 알림도 **flip에만 발화**한다 — transitioning 중엔 held 불변이라 알림 없음(분단위 반전 스팸 해소). 로직 이중화 없이 confluence 단일 소스로 전파.
> 주의: 포지션 chart_analysis 경로는 현재 `build_one_liners`를 confluence 없이 호출(다수결 overall_stance)하므로 이 전파를 받지 못한다 — 포지션 알림까지 히스테리시스를 태우려면 그 지점에 confluence를 넘기는 후속 배선이 필요(WO-54 골든 케이스에서 함께 검토).

### 파라미터 자율 튜닝 (WO-39 hard bound)
`Settings` 필드 + `PARAM_REGISTRY` 등록으로 자율 채택 대상. hard bound 상한은 "진동 흡수 범위를 넘어 추세를 막지 못하도록" 고정:
| param | Settings 기본 | hard_min | hard_max | tighten |
|---|---:|---:|---:|---|
| `directional_flip_margin` | 0.30 | 0.25 | 0.60 | increase |
| `directional_ema_span` | 2.0 | 1.0 | 10.0 | increase |
| `directional_flip_persist` | 2 | 1 | 5 | increase |

`build_confluence`가 오버라이드를 bound로 클램프한다(`_hysteresis_params`). 라이브는 `hysteresis_params_from_settings(settings)`로 주입.

### 종합 신뢰도
`composite_score`는 held 방향의 EMA 점유율 — 전환 관찰 중이면 자연히 낮아져 약한 확신을 정직하게 표현한다(conflicted/insufficient는 순간 우세 비율).

## 출력 스키마 (증거 항목, WO-51 추가분)
```json
{
  "engine": "level",
  "claim": "구조 저항 108.0 · 터치 4",
  "direction": "short",
  "confidence": 72,
  "as_of": "2026-06-19T00:00:00+00:00",
  "base_weight": 12.0,
  "calibration_factor": 1.0,
  "recency_factor": 0.385,
  "decay_class": "structure",
  "htf_factor": 0.55,
  "score": 1.83,
  "is_stale": true
}
```

## WO-54 · 검증 (골든 케이스 + 실증 회귀)

재설계가 실제로 사용자 실증 실패를 고쳤음을 증명하고 회귀를 막는다. `build_confluence(directional_v2=False)`는 재설계 전(v1) 엔진을 재현한다(recency·HTF 배수/가드·히스테리시스 off, mtf는 옛 conf 60 단일 투표) — 전/후 대조와 백테스트 비교에 쓰인다. 테스트: `backend/tests/test_directional_golden.py`.

### 실증 재현 (전 실패 / 후 통과 대조)
| 케이스 | v1 (재설계 전) | v2 (재설계 후) |
|---|---|---|
| **SOXL 반등** (오래된 숏 잔재 + 최근 롱 + 1D 상승) | `short_leaning` (롱 21.3/숏 66.8) — 실증 실패 재현 | **`long_leaning`** (롱 24.2/숏 17.1) |
| **분단위 반전** (4분 간격 롱↔숏 깜빡임) | 방향 반전 ≥3회 | **반전 0회** (held 유지) |
| **HTF 되돌림** (1D 상승 + 4h 저항 거부) | `conflicted` (저항이 상방 잠식) | **`long_leaning`** (되돌림으로 해석) |

### 방향 적중률 (합성 라벨 리플레이, 재설계 전/후)
> ⚠️ **정직성 고지**: 아래는 **합성 라벨 경로**(추세·전환·레인지 + 노이즈, 고정 시드) 리플레이 결과다. 실히스토리 백테스트가 아니다 — 이 리포는 OHLCV 히스토리 수집기가 아직 없다(`docs/Backtest.md`). 기존 `replay_candles`는 setup 시그니처의 R-적중률을 재지, confluence stance를 재지 않으므로, 실히스토리 stance 적중률 측정은 per-bar stance 백테스트 하니스 신설이 필요(후속). 아래 개선은 **합성 시나리오에서 실재**하며 실거래 일반화는 후속 검증 대상이다.

| 레짐 | v1 적중 | v2 적중 |
|---|---:|---:|
| 추세 상승 | 80.0% | **85.0%** |
| 추세 하락 | 65.0% | **82.5%** |
| 전환 상승 | 70.0% | **80.0%** |
| 전환 하락 | 70.0% | 70.0% |
| **전체(방향 구간)** | **71.7%** | **80.8%** |
| **방향 반전 횟수** | **65** | **19** (−71%) |

어느 레짐도 악화 없음. 핵심 개선은 **스퓨리어스 반전 71% 감소**(사용자 실증 불만)와 추세 적중 +9.1%p. (레인지는 독립 롱/숏 근거 공존 케이스로 골든셋 `test_B4`에서 conflicted 검증 — 합성 생성기의 방향 절벽 아티팩트라 위 표에서 제외.)

### 룩어헤드 감사 (WO-34 원칙 재적용)
- **WO-51 recency**: `as_of`가 `now` 이후(미래)면 `age = max(0, …)`로 클램프 → recency ≤ 1.0. 미래 데이터가 가중을 부풀리지 못한다(`test_D1`).
- **WO-53 상태**: 바 i의 `stance_state`는 `prior_state`(i−1) + 현재 분석만 참조. 이후 바 truncate해도 과거 계산 불변(`test_D2`).

### 골든 세트 (교과서 6종, `test_B1~B6`)
하락 지속→하방 / 상승 지속→상방 / 진짜 전환(HTF 포함)→flip / 진짜 레인지→conflicted / 데이터 부족→insufficient / HTF 부재→신뢰도 상한. 각 stance·transitioning·htf_context 고정 assert.

## 후속 (미구현)
- (배선 잔여) 포지션 lifecycle 알림 경로: `chart_analysis`의 `build_one_liners`에 히스테리시스 confluence 주입 → 포지션 stance_flipped도 히스테리시스 상속.
- 실히스토리 stance 백테스트 하니스: per-bar `build_confluence` 리플레이 + OHLCV 수집기 → 방향 적중률 실거래 일반화 검증.
