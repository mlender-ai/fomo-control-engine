# Historical Setup Backtest

WO-FCE-34 adds deterministic historical setup replay. It is descriptive context, not a prediction engine.

## Fixed Policy

- Disclaimer: `과거 통계 · 미래 보장 아님 · 수수료/슬리피지 미반영`
- Entry price: confirmation candle close.
- No lookahead: detectors receive only candles up to the replay index.
- Fractal/zigzag pivots are not treated as actionable until the detector can confirm them from the replay window.
- Outcome horizon: default 48 bars.
- Risk unit: invalidation distance if an eligible structural level exists; otherwise `ATR * 1.5`.
- Same-candle target and invalidation: conservative loss.
- N must always be displayed. `N < 10` is `표본 부족 — 결론 유보`.
- Live calibration and historical backtest stats are never merged into one hit-rate number.

## Signature Schema

Each setup is normalized to:

```text
engine:event_type:strength_class:direction:asset_class:timeframe
```

Examples:

```text
liquidity:sweep_low:Strong:long:crypto:4h
wyckoff:spring_confirmed:conf>=70:long:stock:4h
harmonic:prz_touch:conf>=70:short:crypto:4h
levels:level_touch:score>=70:long:index:1h
```

The same conversion code is used for live analysis and replay output.

## Rate Budget

Setup 시그니처 리플레이는 기존 scout 캔들 창을 사용한다. WO-FCE-88의 방향 stance 검증만 별도 고정 코호트(BTCUSDT·ETHUSDT·SOXLUSDT)에 대해 Bitget 공개 `history-candles`를 페이지 수집한다. 기본 2,196개 4h봉(약 1년)을 영속 캐시에 누적한다. 매일 저부하 잡과 수동 갱신만 허용하며 전 유니버스로 확장하지 않는다.

## WO-FCE-88 · 실제 히스토리 stance 검증

- 입력: Bitget 퍼페추얼 4시간봉 기본 2,196개, 중복 제거·시간 정렬·미확정 마지막 봉 제거. 캐시는 심볼·타임프레임·시작시각 키로 upsert한다. 페이지 경계는 가장 오래된 봉의 버킷 시각을 그대로 재사용하고 응답 후 중복 제거한다. `endTime-1ms`는 Bitget 버킷 정렬에서 확정봉 하나를 건너뛸 수 있으므로 금지한다.
- 판정: 매 확정봉마다 해당 봉까지의 prefix만 `build_chart_analysis → build_confluence`에 전달. 히스테리시스 상태도 봉 순서대로만 전진한다.
- 결과: 6봉 뒤 종가(T+24h). 6봉 stride로 비중첩 표본만 채점한다.
- 성과: 방향 수익에서 자산군별 왕복 수수료·슬리피지를 차감한 net 방향 적중률.
- 표기: 95% bootstrap CI·N·기간·데이터 품질을 항상 병기. N<30 또는 품질<70이면 수치는 보존하되 결론 유보.
- 비교: v1과 v2를 같은 봉·같은 비용·같은 T+24h anchor로 재생한다. CI가 겹치면 개선을 주장하지 않는다.
- 분리: signature는 `directional_v1_real_history_24h`와 `directional_v2_real_history_24h`. WO-54 합성 80.8% 및 라이브 calibration과 합산하지 않는다.
- 품질: gap/비정상 OHLC 이유를 남기고 가장 긴 정상 연속 구간만 판정에 사용한다. 모든 공개 통계 문구는 `format_stat_line`을 경유한다.
- 데이터 한계: 과거 펀딩·OI·청산 히스토리는 없는 값을 0이나 현재값으로 채우지 않고 제외한다.

2026-07-20 최초 420봉 실데이터 사이클(2026-05-10~07-19, 품질 100/100):

| 심볼 | T+24h net 방향 적중 | 95% CI | N |
|---|---:|---:|---:|
| BTCUSDT | 36.6% | 22.0~51.2% | 41 |
| ETHUSDT | 51.1% | 35.6~64.4% | 45 |
| SOXLUSDT | 36.4% | 21.2~51.5% | 33 |

결과가 합성 성적보다 낮아도 그대로 발행한다. 이 표는 방향의 수익 가능성에 대한 과거 관측이며 매매 지시가 아니다.

2026-07-20 기본 1년 창 재수집 결과(페이지 경계 연속성 검증 후):

| 심볼 | 기간 | v1 적중 | v2 적중 | v2 95% CI | N | 판정 |
|---|---|---:|---:|---:|---:|---|
| BTCUSDT | 2025-07-17~2026-07-19 | 44.6% | 46.1% | 40.6~51.5% | 293 | 차이 유의하지 않음 |
| ETHUSDT | 2025-07-17~2026-07-19 | 44.9% | 44.3% | 38.5~50.0% | 296 | 차이 유의하지 않음 |
| SOXLUSDT | 2026-04-09~2026-07-19 | 42.9% | 47.1% | 35.3~60.3% | 68 | 차이 유의하지 않음 |

세 심볼 모두 확정봉 gap 0·비정상 OHLC 0이다. SOXLUSDT는 거래 개시 이후 공개 이력만 존재하므로 1년을 가정하거나 앞 구간을 합성하지 않는다.
