# Security

## API Keys

- Store API keys only in `backend/.env` or a secret manager.
- Never commit `.env`.
- Never paste API secret or passphrase into issues, PRs, logs, or chat.
- Use read-only Bitget keys for v0.4.
- Disable withdrawal permission.
- Prefer IP whitelist for any deployed server.

## Logging

The backend must not log:

- `BITGET_API_KEY`
- `BITGET_API_SECRET`
- `BITGET_API_PASSPHRASE`
- signed request headers

Private API errors are converted to safe messages before being returned to the dashboard.

## Trading Boundary

v0.4 intentionally excludes:

- order placement
- position close endpoints
- batch orders
- trigger orders
- withdrawal endpoints
- autonomous agent execution

Before any semi-automated trading work is introduced, require:

- separate order client module
- explicit confirmation modal
- dry-run mode
- audit log
- integration tests
- user-visible read/write permission warning

## Agent Boundary

- Agentic Research receives an already computed report snapshot.
- Agents may explain, debate, and flag risks, but must not recalculate market data or scores.
- The FOMO Gatekeeper produces a review label only. It does not approve an order.
- No agent is named or implemented as an execution agent.
