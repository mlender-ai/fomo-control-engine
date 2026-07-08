# Statistics — 시그니처 통계 표기·검증 표준 (WO-FCE-36)

FCE가 발행하는 모든 승률·적중률 숫자는 통계적 방어력을 갖춰야 한다. 이 문서는
브리핑·발견 게이트·스카우트 모니터·캘리브레이션이 공유하는 **표기 표준**과 그 뒤의
검증 파이프라인(데이터 무결성 → 비용 → 부트스트랩 CI → OOS/워크포워드 → 레짐 → 중복)을 고정한다.

## 표기 표준

발행 형식은 고정이며 **반드시 `app.backtest.statistics.format_stat_line`** 을 거친다.

```
{라벨} net 1R {승률}% (CI {하한}~{상한}%, N={표본}, {기간}, 레짐 {현재 레짐}) [· OOS 불안정]
```

예: `Strong 스윕 롱 net 1R 64% (CI 51~76%, N=32, 2025-01-01~2025-06-30, 레짐 저변동 횡보)`

기간은 명시적 구간으로 표기한다 — "최근 N일" 표기는 캐시된 오래된 데이터가
현재성을 사칭할 수 있어 금지 (캐시는 `FCE_BACKTEST_CACHE_TTL_HOURS`, 기본 24h로 재계산).

규칙(전 표면 불변):

- **net 만 발행** — 승률·RR은 수수료(taker 왕복) + 슬리피지 차감 후 순손익 기준. gross 는 내부 디버그(`gross_debug`) 전용이며 사용자 노출 금지.
- **CI 없는 승률 표기 금지** — 부트스트랩 CI를 산출하지 못하면 `"CI 미산출 (N=n) — 발행 보류"`.
- 표본이 하한(`sample_floor`, 기본 10) 미만이면 `"표본 부족 (N=n) — 결론 유보"` — 점추정 노출 금지.
- 현재 레짐 슬라이스 표본이 충분하면 그 통계를 우선 표기하고, 부족하면 전체 통계로 폴백하며 그 사실을 명시한다.
- OOS 괴리 > 15%p 인 시그니처는 `· OOS 불안정` 꼬리표를 붙인다.

표기 표준을 우회하는 발행 경로가 없어야 한다. 검증:

```
grep -rn "win_1r_pct" backend/app --include=*.py
```

로 나오는 문자열 포매팅 경로는 전부 `format_stat_line`(또는 `bootstrap_ci_from_counts`) 을 경유해야 한다.

## 검증 파이프라인

### 1. 데이터 무결성 (`backtest/data_quality.py`)

리플레이 전 캔들을 검사한다: 중복 타임스탬프, OHLC 논리 위반(high<low·비양수), 음수 볼륨,
극단 아웃라이어(전 캔들 대비 ±40%), 누락 캔들(갭). 위반은 벌점으로 0~100 점 `data_quality_score`
를 만들고, 하한(`FCE_BACKTEST_DATA_QUALITY_FLOOR`, 기본 70) 미달 심볼은 **통계 발행 금지**
(`source=data_quality_below_floor`). 유효 캔들만 리플레이에 투입한다.

### 2. 거래비용 (`backtest/costs.py`)

`roundtrip_cost_pct = taker 수수료 × 2 + 클래스 슬리피지 + (거래대금 얕으면 상향)`.
1R/2R 도달 판정은 목표가를 `cost_price` 만큼 밀어 순손익 기준으로 채점한다(`outcomes.py`).
config: `FCE_BACKTEST_TAKER_FEE_PCT`(0.06), `FCE_BACKTEST_SLIPPAGE_{CRYPTO,STOCK,INDEX}_PCT`,
`FCE_BACKTEST_SLIPPAGE_SHALLOW_EXTRA_PCT`, `FCE_BACKTEST_SHALLOW_QUOTE_VOLUME_24H`.

### 3. 부트스트랩 CI (`bootstrap_win_ci`)

승/패 시퀀스를 1,000회 리샘플해 95% 신뢰구간을 산출한다. **결정론적** — 시드는 승패
문자열의 `zlib.crc32` 라 동일 입력은 동일 CI.

**게이트·가중은 점추정이 아니라 CI 하한 기준**:

- 발견 게이트(`scout/universe.py`): `win_1r_ci` 하한 ≥ `FCE_UNIVERSE_BACKTEST_MIN_CI_LOW_PCT`(50) 통과. CI 없는 통계는 fail-closed.
- 컨플루언스 가중(`analyst/confluence.py`): 라이브 적중률 CI 하한으로 보정 계수 산정.

이로써 운 좋은 소표본 점추정(예: 5전 4승 80%)이 게이트를 통과하지 못한다.

### 4. OOS + 워크포워드 (`oos_split`, `walk_forward_curve`)

- **OOS**: 시간순 70% 학습 / 30% 검증 분리 집계. 두 구간 승률 괴리 > `FCE_BACKTEST_OOS_UNSTABLE_GAP_PCT`(15%p) 면 `unstable` 플래그.
- **워크포워드**: 180일 창 × 60일 스텝 롤링 승률 시계열 → 성능 부패 곡선(WO-37 입력).
- **제안 승인 화면**: 캘리브레이션 제안(`review/engine.py`)마다 `oos_validation` 첨부 —
  근거 표본을 70/30 분할해 검증기간 지표와 `holds_in_validation`(문제가 검증기간에도 지속되는지) 을 보고한다.

### 5. 레짐 분해 (`backtest/regimes.py`)

결정론적 레짐 라벨링: 200MA 기울기 + ATR 백분위 → 4분류.

| regime | 라벨 |
|---|---|
| uptrend | 상승추세 |
| downtrend | 하락추세 |
| quiet_range | 저변동 횡보 |
| volatile_range | 고변동 횡보 |

히스토리가 짧으면 MA 기간을 적응적으로 축소(하한 40)한다. 시그니처 통계는 레짐별로 분해
발행하고, 브리핑은 **현재 레짐 통계를 우선** 표기(전체는 보조). 크립토 알트는 **BTC 시장 레짐 병기**.

임계값(`FCE_REGIME_*`)의 하드코딩 변경은 제안-승인 경유(WO-22 불변).

### 6. 증거 중복 상관 (`backtest/overlap.py`)

시그니처 쌍의 동시 발생률(같은 캔들 ±2)을 측정해 상관 > `FCE_BACKTEST_OVERLAP_THRESHOLD`(0.7)
인 쌍을 `overlap_group` 으로 묶는다(스윕↔Spring 같은 도메인 사전 포함). 컨플루언스는 같은
그룹에서 최강 1개만 가중 반영하고 나머지는 `"동근원 확인"` 으로 표기만 — **이중 가점 구조적 차단**.

## 금지 (전 표면 불변)

- gross 승률 사용자 노출 금지 (내부 디버그 전용).
- CI 없는 승률 표기 금지.
- 레짐 4분류 임계 하드코딩 변경은 제안-승인 경유.
