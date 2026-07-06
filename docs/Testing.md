# Testing

## Demo Mode

`FCE_DEMO_MODE=true` starts the app with deterministic fixtures and no live exchange/API dependency.

Demo fixtures:
- `healthy_long`: BTCUSDT long, profitable and structurally healthy.
- `critical_short`: ETHUSDT short, high loss and liquidation/invalidaton risk.
- `wyckoff_range`: BASEDUSDT long, range/Spring-style structure for TA layer rendering.

Run locally:

```bash
cd backend
FCE_DEMO_MODE=true FCE_DATABASE_URL=sqlite:////tmp/fce-demo.db python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8875

cd ../dashboard
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8875 npm run dev -- --host 127.0.0.1 --port 8876
```

The UI header shows a `DEMO` badge so demo data cannot be mistaken for live Bitget data.

## Playwright E2E

The Playwright config starts both servers in demo mode. Tests must not call live Bitget, Coinglass, Telegram, or OpenAI.

```bash
cd dashboard
npm run test:e2e
```

Smoke coverage:
- live position list, card sparkline/gauge, verdict/action plan/chart
- TA layer toggle and plan return
- scout scan, symbol analysis, simulator
- calibration modules

## Visual Regression

`dashboard/tests/chart-visual-regression.spec.ts` captures four fixed chart states from the demo fixture:
- plan
- levels
- wyckoff
- harmonic

Snapshot updates are explicit only:

```bash
cd dashboard
npx playwright test tests/chart-visual-regression.spec.ts --update-snapshots
```

When updating snapshots, the PR must state why the visual baseline changed.

## Fixture Extension Rule

When adding a new TA layer or chart grammar, extend the deterministic demo fixture first. Do not add visual tests that depend on live account state or current market data.
