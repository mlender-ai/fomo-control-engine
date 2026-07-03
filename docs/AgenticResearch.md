# Agentic Research

Agentic Research turns one deterministic report snapshot into a structured review meeting. It does not create a trading order.

## API

- `POST /api/research-runs`
- `GET /api/research-runs`
- `GET /api/research-runs/{run_id}`
- `GET /api/research-runs/compare?symbol=BTCUSDT`

Request:

```json
{
  "symbol": "BTCUSDT",
  "timeframe": "4h",
  "mode": "entry_review"
}
```

## Flow

```text
Report
  -> MarketSnapshotRecord
  -> AgentInput
  -> market_structure_analyst
  -> liquidity_analyst
  -> momentum_analyst
  -> bull_researcher
  -> bear_researcher
  -> risk_guardian
  -> fomo_gatekeeper
  -> ResearchRun final label
```

## Determinism

- The report and score JSON are created before the agent run.
- Agents read existing scores, indicators, reason codes, and decision memories.
- Agents do not fetch market data.
- Agents do not recalculate Entry Score or FOMO Index.
- The final label is derived from stored scores and agent outputs.

## Stored Records

- `market_snapshots`: fixed snapshot metadata and score JSON
- `research_runs`: final label, summary, raw input, raw output
- `agent_outputs`: one structured output per agent

## Final Labels

- `cooldown_required`
- `watch_or_small_probe`
- `watch_for_confirmation`
- `wait`

These labels are review states, not order states.
