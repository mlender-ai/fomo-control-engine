# License Notes

This project does not vendor, fork, import, or copy code from the v0.4 reference repositories.

## Reviewed References

- TradingAgents: https://github.com/TauricResearch/TradingAgents
- Vibe-Trading: https://github.com/HKUDS/Vibe-Trading
- FinceptTerminal: https://github.com/Fincept-Corporation/FinceptTerminal
- AutoHedge: https://github.com/The-Swarm-Corporation/AutoHedge

## Implementation Rule

Reference projects may inspire product concepts, naming alternatives, and high-level workflow structure. They must not be used as source for:

- copied code
- copied prompt text
- copied UI layouts or component trees
- copied file structure
- vendored modules
- derivative assets

## FinceptTerminal Constraint

FinceptTerminal is treated as high-risk for code reuse because its public repository states AGPL-3.0 plus commercial-license conditions. For v0.4, only broad product/UX ideas were considered:

- dense finance terminal layout
- multi-area analytics navigation
- research and risk workspace concepts

No Fincept source file, UI implementation, component name, or asset was copied.

## Trading Boundary

License review is separate from safety review. Regardless of license, v0.4 excludes:

- autonomous trading
- order placement
- simulated exchange execution
- private key or wallet handling
- execution-agent naming or behavior
