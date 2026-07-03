# Reference Review

v0.4 references were reviewed as product and architecture inputs only. No external code, component tree, file layout, prompt text, or UI implementation was copied into this repository.

## TradingAgents

Source: https://github.com/TauricResearch/TradingAgents

Adopted ideas:

- Specialized analyst roles
- Bull and Bear researcher debate
- Risk review before final decision wording
- Persistent decision memory
- Checkpoint-style run records
- Warning that LLM/agent output is not deterministic trading advice

FOMO Control adaptation:

- The final gate is `fomo_gatekeeper`, not a Portfolio Manager approving trades.
- The run is an entry-review meeting over a deterministic score snapshot.
- There is no trader agent and no exchange execution path.

## Vibe-Trading

Source: https://github.com/HKUDS/Vibe-Trading

Adopted ideas:

- Shadow Account concept
- Comparing actual trades against behavior-derived rules
- Noise/FOMO/late-exit attribution
- Liquidation heatmap as a separate skill
- Validation patterns such as Monte Carlo, Bootstrap Sharpe CI, and Walk Forward checks

FOMO Control adaptation:

- Shadow rules are extracted only from completed local trades.
- Liquidation Intelligence is a proxy interpretation layer and does not change Entry Score directly.
- Validation uses stored trade outcomes and produces warnings when the sample is weak.

## FinceptTerminal

Source: https://github.com/Fincept-Corporation/FinceptTerminal

Adopted ideas:

- Dense terminal-style information surfaces
- Research, portfolio, risk, and workflow-oriented dashboard areas

FOMO Control adaptation:

- Dashboard pages are independently implemented in Next.js.
- No Fincept code, UI component, file structure, or visual implementation was copied.

## AutoHedge

Source: https://github.com/The-Swarm-Corporation/AutoHedge

Adopted ideas:

- Agent responsibility separation
- Risk-first flow
- Structured outputs and logging mindset

FOMO Control adaptation:

- `risk_guardian` explains risk before the final label.
- There is no execution agent, wallet, private key workflow, order builder, or venue adapter.
