# Terminal UX

## Keyboard Model

Global shortcuts:

- `Cmd/Ctrl+K`: open command palette
- `/`: open command palette when focus is not inside an input
- `G` then `D`: dashboard
- `G` then `M`: markets
- `G` then `R`: research runs
- `G` then `P`: positions
- `G` then `J`: journal
- `G` then `S`: shadow account
- `G` then `V`: validation lab

Command palette actions:

- Open core routes
- Open BTC/ETH report views
- Create a BTC research run
- Run read-only Bitget position sync
- Extract shadow profile
- Run deterministic BTC validation

## Workspace Pattern

Each page follows the same terminal layout:

1. Page header with context and one primary refresh/action group.
2. Metric strip for immediate numeric orientation.
3. Main data table or score panel.
4. Supporting explanation, raw JSON, warning, or history panel.

This keeps repeated workflows predictable:

- scan status
- identify warning
- inspect deterministic evidence
- record or review without order execution

## Loading, Error, Empty State

- Loading states use compact text in the same panel where data will appear.
- Empty states explicitly say no data exists yet.
- Errors are shown as terminal warnings instead of blocking the full page.
- Long JSON is collapsed by default through `TerminalRawJson`.

## Copy Boundaries

The design may use financial terminal principles:

- dense multi-panel workspace
- command palette
- keyboard routing
- status-first information architecture
- tabular market data

The design must not copy:

- Bloomberg Terminal trademark, logo, proprietary screen layout, text, color pairings, icons, or exact panel arrangement
- FinceptTerminal source code, component structure, or AGPL-covered implementation details
- TradingAgents, Vibe-Trading, or AutoHedge execution/order concepts
