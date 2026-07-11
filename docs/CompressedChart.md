# Compressed Live Chart (압축 라이브 차트 + 스탠스 리본)

WO-FCE-55. 필터를 눌러 TA를 하나씩 확인하는 방식을 폐기하고, "지금 롱이냐 숏이냐 · 익절할 자리냐"를 한 화면에서 즉답하는 단일 압축 차트로 대체한다. Part A(게이지 판정, `backend/app/analyst/gauges.py`) 문서.

## 제품 원칙
- **갱신 기준: 시간봉 확정.** 4h 캔들 마감 시 확정 갱신. 마감 전은 "잠정"으로 흐리게(`bar_state.provisional`). 1분봉 실시간은 향후 확장(이 WO 범위 밖).
- **레이어는 사용자가 고르지 않는다.** 엔진이 지금 방향을 결정한 상위 것만 자동 선별(`tier2_overlays`).
- **read-only 불변.** 게이지는 판단 표시일 뿐 주문과 무관. 방향 지시 문형 금지.
- **분석은 시장 중립, 포지션은 오버레이다.** 스탠스·리본·HUD·근거·다음 가격은 포지션 유무와 무관한 1층이다. 진입·PnL·익절 압력·시장 대비 정합 여부만 2층으로 덧붙인다.
- **스탠스와 익절 압력은 다른 축이다.** 스탠스는 캔들 위 리본, 익절 압력은 포지션이 있을 때만 우측에 표시한다.

## 레이어 3티어 (성적이 자격을 정한다)
- **티어1 · 항상 표시** (관측 사실, 적중률 무관): 캔들 + 거래량 + 볼륨(POC) + 유동성(미스윕 풀·확정 스윕) + 레벨(상위 지지/저항).
- **티어2 · 방향 기여 + 차트엔 상위 1~2개만**: 와이코프·하모닉·수급 중 **백테스트 시그니처 lifecycle_state == `validated`** (WO-37)인 engine·direction만 자격. 그중 현재 held 방향을 가장 강하게 민(기여 score 상위) 1~2개만 오버레이. candidate(표본 부족)·degraded·quarantined는 미표시 — 하모닉 저성적이면 거의 안 뜨는 게 정상 동작. stance가 conflicted/insufficient면 방향이 없으므로 오버레이 0개. 자동 교체는 이 WO 범위 밖(고정).
- **티어3 · 삭제**: 조건경로 등 성적 없는 표면 → 압축·프로 양쪽 제거(Part B).

## API (Part B 소비)
`GET /api/scout/{symbol}/analysis` · `GET /api/live/positions/{id}` 응답의 `gauges` 필드 — **양쪽 동일 스키마**(포지션·스카우트 동일 컴포넌트 지원).

```json
{
  "direction": {
    "active": true, "needle": 0.5, "confidence": 0.5,
    "stance": "long_leaning", "stance_label": "상방 우세",
    "transitioning": false, "target": null, "flip_progress": 0.0, "candles_in_state": 3,
    "reason": "유동성: 저점 Strong 스윕 확인"
  },
  "market_view": {
    "stance": "long_leaning", "stance_label": "상방 우세",
    "why": "저점 청소 후 반등 구조", "counter": "상단 저항 근접",
    "next_price": {"label": "상단 저항", "price": 110287.84, "detail": "도달 시 시장 구조 재평가"}
  },
  "position_context": {
    "active": true, "alignment": "opposed",
    "headline": "숏 보유 중 · 시장은 상방 우세", "detail": "역행 포지션"
  },
  "take_profit": {
    "active": true, "level": "높음", "pressure": 0.82,
    "reason": "목표 풀 청소 확인 — 상단 유동성 소진",
    "components": [{"key": "liquidity_exhaustion", "label": "목표 유동성 소진", "score": 1.0, "phrase": "…"}]
  },
  "tier2_overlays": [{"engine": "wyckoff", "engine_label": "와이코프", "claim": "Spring 74", "price": 96.0, "direction": "long", "qualification": "validated"}],
  "bar_state": {"provisional": true, "minutes_to_close": 178, "bar_close_at": "…"},
  "policy": "게이지는 판단 표시입니다. 주문과 무관하며 판단은 사용자 몫입니다."
}
```

## 스탠스 리본
입력은 WO-50~57의 확정 캔들 기반 `stance_state`다. 리본은 볼린저 중간선(없으면 종가 경로)을 시각 앵커로 재사용하며 새 지표를 계산하지 않는다.

```
long_leaning=초록 · short_leaning=빨강 · conflicted/insufficient=회색
transitioning=황색 · provisional tail=점선/흐림
```
- 색은 확정 캔들에서만 바뀐다. 잠정 캔들은 마지막 구간만 점선으로 덧붙인다.
- 리본은 2.9px 곡선과 confidence 6~14% 후광으로 표시한다. 충돌 상태는 직전 또는 우세 근거 방향의 옅은 색을 유지하고, 전환 관찰 중은 황색 점선이다.
- `last_flip_at`에 도트와 미세 세로선을 표시하며 hover에는 저장된 flip 사유 1줄만 노출한다.
- HUD는 현재 스탠스, 유지 캔들 수, 상·하방 증거 개수를 표시하고 클릭 시 TA 1줄 판정을 펼친다.

## 검증 이벤트 알약
알약은 `validated` 시그니처, 확정 캔들 이벤트, 시그니처별 confidence 하한을 모두 통과해야 한다. 최대 6개이며 hover에 신뢰도와 실측 win@1R/N을 표시한다. N<30은 "표본 축적 중"으로 표시한다. 후보 시그니처는 이 경로에서 조회하지 않는다.

알약이 비었던 원인은 두 가지였다. 포지션 상세은 `historical_backtest`를 전달하지 않았고, 스카우트는 현재 활성 시그니처 통계만 전달해 과거 확정 이벤트와 통계가 연결되지 않았다. 이제 저장된 validated 통계를 `event_stats`로 별도 조회해 두 경로가 공유한다. 응답의 `event_pill_audit`는 확정 이벤트 수, validated 통계 수, 최종 렌더 수를 노출해 자격 미달과 배선 오류를 구분한다.

## 익절 압력 게이지 (수익 반납 리스크)
방향과 **독립**. 포지션 보유 시에만 활성(`position.direction` 필요) — 진입 전엔 `active:false`(Part B 흐림). 결정론 3입력, profit 방향은 롱=상단/숏=하단으로 대칭.

| 성분 | 가중 | 공식 (0~1) |
|---|---:|---|
| 목표 유동성 소진 | 0.40 | profit 방향(롱=buy_side, 숏=sell_side) 미스윕 풀 잔여: 0개→1.0, 1개→0.6, ≥2개→0.2. profit 방향 확정 스윕 존재 시 +0.2(cap 1.0). 풀 데이터 없으면 성분 제외(가중 재정규화) |
| 장벽 근접 | 0.35 | profit 방향 최근접 장벽(저항/지지 레벨 + 하모닉 PRZ mid)까지 거리%: `clamp(1 − d% / 3.0, 0, 1)` — 3% 이상 0, 0% 1 |
| 거래량 둔화 | 0.25 | `clamp((1.2 − 상대거래량) / 0.8, 0, 1)`; volume_state=drying_up이면 최소 0.8 |

```
pressure = Σ(성분 × 가중) / Σ(존재 성분 가중)
level    = 낮음 (< 0.35) · 중간 (< 0.65) · 높음 (≥ 0.65)
reason   = 가중 기여 최대 성분의 plain 문구 1줄
```
점수 남발 금지 — UI 노출은 level + reason 1줄이 기본, components는 디테일 확장용.

## 잠정/확정 (`bar_state`)
`last_candle_at + TF분` 전이면 `provisional: true` + `minutes_to_close`. Part B는 잠정 시 게이지·오버레이를 흐리게 + "잠정 · 마감까지 Nh" 표기, 마감 시 확정 갱신.

## 한계·후속
- 장벽 근접 3%·거래량 1.2/0.8 상수는 4h 크립토 초기값 — 백테스트 튜닝 시 WO-39 파라미터 편입 후보.
- 익절 입력 확장(펀딩 극단·미실현 R 배수 등)은 후속.
