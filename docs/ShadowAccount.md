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

## Current Rule Extraction

v0.4 extracts lightweight rules from completed trades:

- high Entry Score winners
- winners where score did not collapse after entry
- low Entry Score losing trades as FOMO candidates
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

## Limits

- Shadow rules are not trading signals.
- Missing trades or manual journal gaps can distort the profile.
- v0.4 trades do not yet store entry FOMO Index or entry Risk Score, so deeper attribution is deferred.
