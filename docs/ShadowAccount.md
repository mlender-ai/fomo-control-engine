# Shadow Account

Shadow Account extracts behavior rules from completed trades. It is designed to answer: "When did my own trades work, and when did FOMO hurt me?"

## API

- `POST /api/shadow/extract`
- `GET /api/shadow`
- `GET /api/shadow/{shadow_id}`
- `POST /api/shadow/{shadow_id}/compare`

Default extraction request:

```json
{
  "min_trades": 10,
  "min_profitable_trades": 5
}
```

## Sample Guard

Extraction refuses to run unless both thresholds are met. This prevents the system from inventing rules from a small journal.

## FOMO Index v2 (WO-FCE-90)

신규 진입은 진입 순간에만 아래 6개 사실을 `Position.entry_fomo_snapshot`에 고정하고, 종료 거래에 그대로 복사한다.

- 계획가(`plan_price`)와 방향 보정 이탈률(`chase_pct`)
- 마지막 보고서 생성→진입 간격(`report_to_entry_minutes`)
- 스카우트/시나리오 경유 여부(`scout_originated`)
- 당시 held stance와 진입 방향의 정합(`stance_alignment`)
- 당시 보고서 상태 라벨(`entry_state_label`)

FOMO Index는 chase 40, 진입 간격 20, 미경유 15, stance 역행 25의 결정론 합성이다. 각 성분의 severity·가중치·기여를 함께 저장하며 임계 기본값은 65다. 가중치와 임계는 `PARAM_REGISTRY`의 hard bound를 갖지만, 이 점수는 Entry Score·방향 판정·자동 진입에 주입되지 않는다.

데이터가 없는 성분은 `available=false`로 남고 가용 성분만 정규화하되, 월간 귀속에는 **4개 성분이 모두 있는 complete 스냅샷만** 포함한다. 과거 거래는 소급 추정하지 않는다.

## Current Rule Extraction

v0.4 extracts lightweight rules from completed trades:

- high Entry Score winners
- winners where score did not collapse after entry
- FOMO Index가 임계 이상이며 손실로 종료된 거래
- late exit attribution when exit score dropped materially

## Stored Record

`shadow_profiles` stores:

- profile ID
- sample counts
- date range
- human-readable profile text
- extracted rules
- common mistakes
- attribution breakdown

## Decision Memory

After extraction, a summary memory is created so future research runs can show relevant behavioral history.

## 월간 귀속

계좌 성적표는 `FOMO 비용 X USDT (거래 n건, 관측 가능 손실 대비 y%)`를 표시한다. complete 신규 진입 N이 표본 하한 미만이면 금액을 발행하지 않고 `표본 부족 — 결론 유보`를 표시한다. 구 `entry_score<65 + loss` 프록시는 연속성 감사 수치로 한 번 비교할 뿐 신규 귀속에 사용하지 않는다.

FOMO 판단은 `fomo_entry`로 기존 Judgment Ledger에 기록하고 종료 시 손익 부호와 함께 채점한다. 이는 과거 행동 분류이며 매매 지시가 아니다.

## Limits

- Shadow rules are not trading signals.
- Missing trades or manual journal gaps can distort the profile.
- 진입 스냅샷 이전 거래는 `fomo_index=null`이며 월간 귀속 N에서 제외한다.
- FOMO Index는 행동 관측치이며 인과관계나 손실 예측을 단정하지 않는다.
