# Compressed Live Chart (압축 라이브 차트 + 2게이지)

WO-FCE-55. 필터를 눌러 TA를 하나씩 확인하는 방식을 폐기하고, "지금 롱이냐 숏이냐 · 익절할 자리냐"를 한 화면에서 즉답하는 단일 압축 차트로 대체한다. Part A(게이지 판정, `backend/app/analyst/gauges.py`) 문서.

## 제품 원칙
- **갱신 기준: 시간봉 확정.** 4h 캔들 마감 시 확정 갱신. 마감 전은 "잠정"으로 흐리게(`bar_state.provisional`). 1분봉 실시간은 향후 확장(이 WO 범위 밖).
- **레이어는 사용자가 고르지 않는다.** 엔진이 지금 방향을 결정한 상위 것만 자동 선별(`tier2_overlays`).
- **read-only 불변.** 게이지는 판단 표시일 뿐 주문과 무관. 방향 지시 문형 금지.
- **두 게이지는 다른 축 — 합치지 않는다.** "롱 우세 + 익절 압력 높음"(끌되 일부 실현)이 정상 표현.

## 레이어 3티어 (성적이 자격을 정한다)
- **티어1 · 항상 표시** (관측 사실, 적중률 무관): 캔들 + 거래량 + 볼륨(POC) + 유동성(미스윕 풀·확정 스윕) + 레벨(상위 지지/저항).
- **티어2 · 방향 기여 + 차트엔 상위 1~2개만**: 와이코프·하모닉·수급 중 **백테스트 시그니처 lifecycle_state == `validated`** (WO-37)인 engine·direction만 자격. 그중 현재 held 방향을 가장 강하게 민(기여 score 상위) 1~2개만 오버레이. candidate(표본 부족)·degraded·quarantined는 미표시 — 하모닉 저성적이면 거의 안 뜨는 게 정상 동작. stance가 conflicted/insufficient면 방향이 없으므로 오버레이 0개. 자동 교체는 이 WO 범위 밖(고정).
- **티어3 · 삭제**: 조건경로 등 성적 없는 표면 → 압축·프로 양쪽 제거(Part B).

## API (Part B 소비)
`GET /api/scout/{symbol}/analysis` · `GET /api/live/positions/{id}` 응답의 `gauges` 필드 — **양쪽 동일 스키마**(포지션·스카우트 동일 컴포넌트 지원).

```json
{
  "direction": {
    "active": true, "needle": 0.5, "stance": "long_leaning", "stance_label": "롱 우위",
    "transitioning": false, "target": null, "flip_progress": 0.0, "candles_in_state": 3,
    "reason": "유동성: 저점 Strong 스윕 확인"
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

## 방향 게이지 (롱↔숏)
입력은 WO-50~54 재설계된 confluence(시간가중·HTF앵커·히스테리시스 반영 stance) — **게이지는 번역만 하고 새 판정 로직이 없다**.

```
needle = (long_score_ema − short_score_ema) / (long_score_ema + short_score_ema)   ∈ [−1, +1]
```
- EMA 점수(WO-53) 기반이라 순간 스파이크에 바늘이 튀지 않는다. insufficient → 비활성·중앙(0).
- `transitioning=true`면 UI는 바늘 떨림 + "전환 관찰 중"(`flip_progress` 진행도) — 억지 방향 금지.
- `reason` 1줄 = held 방향 상위 기여 증거의 plain 문구(`엔진라벨: claim`, WO-43 문법 계승, 점수 미노출). conflicted는 "롱/숏 근거가 팽팽함 — 방향을 고르지 않음".

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
- 포지션 경로는 `historical_backtest`가 페이로드에 없어(스카우트 전용 강화) `tier2_overlays`가 항상 [] — validated 증명 불가 시 미표시가 원칙이므로 안전 방향 강등. 포지션에도 백테스트 컨텍스트가 붙으면 자동 활성.
- 장벽 근접 3%·거래량 1.2/0.8 상수는 4h 크립토 초기값 — 백테스트 튜닝 시 WO-39 파라미터 편입 후보.
- 익절 입력 확장(펀딩 극단·미실현 R 배수 등)은 후속.
