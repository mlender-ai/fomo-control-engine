# UX/UI Design System

## Product Stance

FOMO Control Engine is not an auto-trading bot. The UI should repeatedly answer one question:

> Is this planned entry supported by data, or is it an emotional chase?

The dashboard prioritizes fast judgment over decorative presentation. It uses dense panels, numeric hierarchy, and explicit warning states so the user can scan market data, entry score, FOMO index, risk, position state, research runs, journal records, shadow profile, and validation results without changing mental context.

## Visual Direction

- Bloomberg Terminal-inspired density, not Bloomberg-owned branding or proprietary UI.
- Astryx accessibility and component discipline.
- Dark-first graphite surface with amber command accents, cyan/blue data accents, green/red PnL states, and purple/agent accents.
- Low-radius controls and panels. Cards are used only as functional panels, not decorative marketing sections.
- Numbers and status labels appear before explanatory text.

## Core Surfaces

- Top Command Bar: app identity, current symbol context, command palette trigger, provider/API status, local time.
- Left Rail: Monitor, Journal, and Lab groupings.
- Dashboard: market monitor, FOMO gate, position monitor, research run tape, attention queue.
- Markets: score/risk watchlist for default symbols.
- Ticker Detail: deterministic score breakdown, data quality, report text, raw snapshot JSON.
- Research Runs: agentic review run creation, timeline, decision memory.
- Positions: manual record input, read-only monitor update, internal exit record only.
- Journal: closed trade table and selected review.
- Shadow Account: extracted behavior profile, rules, FOMO patterns, attribution.
- Validation Lab: deterministic validation runs, warnings, raw result payload.
- Settings: provider boundary, safety contract, keyboard workflow.

## Safety UX

- Use "Record Exit" or "internal exit record", never language that implies an exchange order is placed.
- Show "read-only" and "no order execution" in persistent locations.
- LLM/agent text must be described as explanation of deterministic score JSON, not score generation.
- High FOMO or high risk states should be visible, but not sensational.
