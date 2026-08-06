# Hyperliquid whale observation

This integration is read-only. It calls only Hyperliquid's unauthenticated `POST /info` endpoint and never signs or submits an order.

## Automatic discovery

- The worker reads the public dataset used by `https://app.hyperliquid.xyz/leaderboard` once per hour.
- It scans the full leaderboard and fills the watchlist automatically. Manual and bot-added wallets are preserved and consume slots before discovery wallets.
- Default discovery hygiene requires a 30-day profit of at least 100,000 USDT, ROI of at least 2%, account value of at least 1,000,000 USDT, and volume of at least 10,000,000 USDT.
- Monthly turnover above 250 times account value is excluded to reduce market-maker and high-frequency flow contamination.
- Discovery wallets that leave the selected set are deactivated. Their events, judgments, and candidate statistics are retained.
- The engine dashboard exposes current long/short notional, 24-hour signed flow, a 72-hour two-hour-bucket histogram, symbol exposure, and the latest large fills. The flow, metrics, and fill tape share one instrument filter; raw Hyperliquid coins such as `XYZ:SNDK` remain searchable even when they cannot map to an FCE `*USDT` chart symbol.
- The latest-fill tape is a presentation view over the append-only event ledger. It combines only same-wallet, same-instrument, same-action fills observed inside a 60-second window, retains the raw event IDs and fill count, and round-robins instruments so one fragmented execution cannot hide every other symbol. The API also retains a separate latest-ten tape per instrument, so a symbol outside the global twenty-row tape is not mistaken for having no fills. The raw ledger is never rewritten or deleted by this compaction.

Manual `/whale add` registration remains available only as an override for a known public master or sub-account address. No API key, agent key, private key, or transaction hash is accepted as tracking input.

## Collection budget

- Maximum active wallets: 20.
- Default poll interval: 30 seconds. Values below 30 seconds are rejected by configuration validation.
- Telegram entry alerts use a fixed three-minute event-time window per wallet. The worker keeps polling every 30 seconds, buffers confirmed `open`, `increase`, and `flip` fills, and emits one message only when at least two fills belong to the same wallet inside that window. A single fill expires silently from the alert buffer while remaining intact in the append-only event ledger. Pending batches survive a worker restart through notification-state persistence.
- A batch message groups identical coin/direction/action fills into one line with fill count, summed observed notional, and size-weighted average fill price. Different instruments and opposing sequential fills from the same wallet remain separate lines inside the same notification instead of being presented as simultaneous exposure.
- `clearinghouseState`: weight 2 per wallet.
- `userFillsByTime`: base weight 20 per wallet plus the official per-item response weight.
- With 20 wallets and no returned fill items, the conservative base estimate is 880 weight/minute against the official 1,200 weight/minute IP budget. The dashboard publishes this configured-capacity estimate and whether it remains inside the official base budget.
- Fill polling starts at the wallet's last stored fill timestamp. First registration uses a bounded seven-day lookback. Worker failures use the common exponential backoff.

The relevant settings are `FCE_HYPERLIQUID_WHALE_TRACKING_ENABLED`, `FCE_HYPERLIQUID_WHALE_DISCOVERY_ENABLED`, `FCE_HYPERLIQUID_WHALE_DISCOVERY_INTERVAL_SECONDS`, `FCE_HYPERLIQUID_WHALE_POLL_INTERVAL_SECONDS`, `FCE_HYPERLIQUID_WHALE_ALERT_BATCH_WINDOW_SECONDS`, `FCE_HYPERLIQUID_WHALE_MIN_SIZE_USD`, and `FCE_HYPERLIQUID_WHALE_MAX_WALLETS`.

## Data and decision boundaries

- Events are derived only after a fill appears in `userFillsByTime`.
- Position flips retain their transition meaning everywhere: a fill that crosses a short position through zero is shown as `숏→롱 전환`, and the inverse is shown as `롱→숏 전환`. A later opposite flip from the same wallet is sequential position history, not proof of simultaneous long and short exposure.
- Historical chart markers are anchored to the closed FCE candle that contains the confirmed fill and never moved to a later candle.
- A confirmed fill in the still-open chart window is rendered at its observed fill price on the chart's right edge with a `실시간` label. Its actual event timestamp is retained in the marker and click detail; it is not assigned to the unfinished candle as if that candle were final.
- The minimal chart refreshes its confirmed-fill overlay every 30 seconds for pure crypto symbols. Long fills use green upward triangles and short fills use red downward triangles. Every triangle is filled consistently; `+` means entry/increase and `−` means reduce/close. A surrounding ring means that the group contains a wallet that passed the 28-day validation gate.
- Selecting a chart marker shows the contributing wallet aliases and shortened public addresses, event actions, observed notionals, fill prices, event times, and validation state. Aliases remain explicitly unverified.
- The minimal chart exposes 15-minute, 1-hour, 4-hour, 12-hour, and daily confirmed-candle views. The opaque current-price line is intentionally omitted there so it cannot be confused with a whale fill. Current mark price remains a numeric readout; no unfinished OHLC candle is fabricated from a single mark price.
- The chart selects the most recent eight event groups by event time, not the eight largest notionals.
- Raw coins that cannot map to a plain FCE `*USDT` symbol retain `coin` as their instrument identity for repository queries, current exposure, recent fills, and chart context. A live position chart still requires an explicit FCE position/symbol route; the service does not fabricate a crypto mapping for stock-like raw coins.
- Every wallet starts as a candidate. Its events are observation data, not a follow signal.
- Recent-fill rows always publish the wallet label, validation state, sample size, and compacted fill count. Candidate events remain visible for audit but are not direction-eligible.
- Discovery first filters the public leaderboard by account size, 30-day PnL/ROI, volume, and turnover. It then inspects current BTC/ETH positions for the top scan cohort and reserves directional slots across BTC/ETH long and short before filling the remaining slots by quality.
- The discovery audit surface publishes rows scanned, eligible candidates, position-inspected candidates, selected direction coverage, and the reason each wallet entered the validation cohort. This prevents a profitable-long-only leaderboard slice from being presented as the whole whale market.
- Only a promotion approved through the existing veto-window flow can make a wallet `validated`.
- Whale promotion requires all three gates: at least 28 elapsed validation days, `N >= 30`, and a net 1R confidence-interval lower bound of at least 55%.
- Leaderboard 30-day ROI/PnL is a discovery input, not validation evidence. FCE separately publishes follow-trade win rate, confidence interval, cumulative realized R, average R, and the 28-day progress for each wallet.
- The dashboard filters wallets into validating, review-ready, trusted, and excluded groups. A high leaderboard rank alone never enters the trusted group.
- Only validated wallets may contribute a low-weight onchain item to confluence, use an emphasized chart marker, or emit a validated warning alert.
- Labels are user-provided aliases and never claim verified ownership or identity.

Official API references: Hyperliquid info endpoint and rate-limit documentation.

---

## 선정 기준 확정 (WO-FCE-ENTRY-THROUGHPUT-01 작업 5, 2026-08-03)

> **고래는 승률로 뽑지 않는다.** 월간 PnL·ROI·계좌규모 복합 점수(`quality_score`)로 뽑는다.

사용자 질문("상위 승률 고래 선정하고 있는 거 맞아?")에 대한 코드 기준 답이다.

### 선정 경로

`discover_whale_leaderboard` 잡 → `services.runtime.discover_whales`
→ `onchain/service.py::discover` → `onchain/hyperliquid/leaderboard.py::discover_leaderboard_wallets`
→ **`select_candidates(rows, criteria)`** → `select_directional_cohort(...)`

### 1단계 — 자격 필터 (`select_candidates`, 전부 통과해야 후보)

| 조건 | 설정 |
| --- | --- |
| 계좌 가치 ≥ | `hyperliquid_whale_discovery_min_account_usd` |
| 월간 PnL ≥ | `hyperliquid_whale_discovery_min_month_pnl_usd` |
| 월간 ROI ≥ | `hyperliquid_whale_discovery_min_month_roi` |
| 월간 거래대금 ≥ | `hyperliquid_whale_discovery_min_month_volume_usd` |
| 회전율(거래대금/계좌) ≤ | `hyperliquid_whale_discovery_max_turnover` |

**승률 조건은 없다.**

### 2단계 — 정렬 점수

```python
quality_score = log10(max(1, month_pnl)) * 30 + min(month_roi, 1.0) * 100 + log10(max(1, account_value)) * 8
```

PnL 절대액 · ROI · 계좌 규모의 가중합이다. 승률은 들어가지 않는다.

### 왜 승률이 아닌가

**하이퍼리퀴드 리더보드 API 가 승률 필드를 주지 않는다.** 응답에는 창별 `pnl`·`roi`·`vlm`
만 있다. 승률로 뽑으려면 지갑별 체결 이력을 직접 재구성해야 하며, 그것은 이 WO 범위 밖이다.

우리가 표기하는 **추종 승률은 선정 후 사후 채점 결과**이며 선정에 피드백되지 않는다.
알림 문구도 둘을 구분해 쓴다:

```
선정: quality_score 기준 · 사후 채점 승률 40.0% (N=10) · 표본 부족
```

붙여 쓰면 "승률로 고래를 뽑았다"로 오해된다. 현재 N=10 은 표본 부족이므로
**선정 기준 변경의 근거로 쓸 수 없다.**

### `service.py:439` 의 `size_usd` 정렬은 선정과 무관

```python
ranked = sorted(grouped, key=lambda item: item.size_usd, reverse=True)
```

이 줄은 차트 마커 묶음 안에서 체결을 크기순으로 **표시**하는 코드다. 지갑 선정에 쓰이지
않는다. D4 의 의심(규모 기준 선정)은 이 줄에 대해서는 해소된다 — 다만 실제 선정 기준
역시 승률이 아니라 PnL·ROI·규모 복합이므로, "규모가 섞여 있다"는 취지 자체는 맞다.

승률 기반 선정으로 전환할지는 별도 판단이다(이 WO 는 규명까지).

## 선정 기준과 승률의 관계 (WO-FCE-OBSERVATION-INTEGRITY-01 Phase 5)

> **선정은 `quality_score`(월간 PnL·ROI·계좌규모)로 한다. 승률은 사후 채점 지표이며 선정에 쓰지 않는다.**

### 승률 데이터 가용성: 가능

리더보드 응답(`windowPerformances`)에는 승률 필드가 **없다** — pnl·roi·vlm 뿐이다.
그러나 `userFillsByTime` 이 체결별 `closedPnl` 을 주고, 수집기가 이미
`whale_events.payload.payload.closed_pnl` 로 저장하고 있다.

실측 2026-08-05:

| 항목 | 값 |
| --- | --- |
| `close`/`reduce` 이벤트 | 6,382건 |
| `closed_pnl` 보유율 | **100%** |
| 지갑 수 | 62개 (표본 20건 이상 33개) |
| 전체 승률 | 64.7% |

### 왜 `quality_score` 에 승률 항을 넣지 않는가

넣을 수 **있지만** 실측이 넣지 말라고 말한다. 승률과 수익성이 역상관인 사례가 실재한다.

| 지갑 | 승률 | 누적 closed PnL |
| --- | --- | --- |
| `0xd04f9719…` | **4.3%** (n=47) | **+$2,108,265** |
| `0x2437529…` | 100.0% (n=204) | +$1,227,485 |
| `0x77375a8c…` | 51.1% (n=174) | **−$415,726** |
| `0xfc667adb…` | 36.7% (n=251) | −$135,835 |

승률 4.3%로 210만 달러를 번 지갑은 **비대칭 손익 트레이더**다. 승률 항을 점수에 더했다면
이 지갑이 강등됐을 것이다. 반대로 승률 51%인 지갑은 41만 달러를 잃었다.

따라서 승률은 **병행 관측(A/B)** 으로만 노출하고 선정 로직은 건드리지 않는다.
선별 기준 변경은 이 관측이 충분히 쌓인 뒤 별도 WO 에서 판단한다.

### 사후 재선별의 재료

`app/onchain/win_rate.py` 가 지갑별 `{sample_size, wins, win_rate_pct, closed_pnl_usd,
sample_low, profitable}` 을 낸다.

- 표본 20건(`MIN_SAMPLE`) 미만이면 `win_rate_pct=None` — **모르면 모른다고 낸다.**
- 승률과 누적 손익을 **분리해서** 낸다. 하나로 합치면 위 표의 지갑들을 잘못 판단한다.
- 대시보드 `whale_dashboard` 응답: 지갑별 `observed_win_rate`, 전역 `selection_disclosure`.

### 표기 규약

화면·알림은 `선정: quality_score(PnL·ROI·규모) · 승률은 사후 채점(N=xx)` 형식으로 고지한다.
승률 수치를 선정 근거처럼 배치하지 않는다.
