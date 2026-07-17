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

## Local Production Build

실행 중인 프로덕션 서버와 같은 `.next` 디렉터리에 다시 빌드하지 않는다. 서버가
이전 HTML을 유지한 상태에서 청크만 교체되면 모든 화면의 CSS/JS가 깨질 수 있다.

```bash
cd dashboard
# 실행 중인 8876 서버를 먼저 Ctrl-C로 종료
npm run build
npm run start:local

# 다른 터미널에서 런타임 청크 정합성 확인
npm run check:local-assets
```

`npm run build`는 8876 포트가 열려 있으면 의도적으로 실패한다.
Playwright 프로덕션 빌드는 `FCE_NEXT_DIST_DIR=.next-e2e`를 사용하므로 실행 중인
로컬 `.next` 서버와 파일을 공유하지 않는다. `build:e2e` 래퍼가 Next의 타입 설정
자동 변경도 빌드 직후 원복한다.

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
