import { expect, test } from "@playwright/test";

test.afterEach(async ({ page }) => {
  // Some panels refresh in the background. Let the test finish without a
  // late route.fetch callback leaking into the next isolated page fixture.
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test("PWA metadata, icons, and same-origin API work on mobile", async ({ page, request }) => {
  const manifestResponse = await request.get("/manifest.webmanifest");
  expect(manifestResponse.ok()).toBe(true);
  const manifest = await manifestResponse.json();
  expect(manifest).toMatchObject({
    name: "FOMO Control Engine",
    short_name: "FOMO Control",
    display: "standalone",
    start_url: "/?source=pwa"
  });
  expect(manifest.icons).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ sizes: "192x192", type: "image/png" }),
      expect.objectContaining({ sizes: "512x512", type: "image/png" })
    ])
  );

  for (const icon of manifest.icons) {
    const iconResponse = await request.get(icon.src);
    expect(iconResponse.ok(), icon.src).toBe(true);
    expect(iconResponse.headers()["content-type"]).toContain("image/png");
  }

  const apiResponse = await request.get("/api/system/status");
  expect(apiResponse.ok()).toBe(true);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.locator('link[rel="manifest"]')).toHaveAttribute("href", /manifest\.webmanifest/);
  await expect(page.getByTestId("position-strip")).toBeVisible({ timeout: 30_000 });
  const toolbarBox = await page.locator(".cockpitToolbar").boundingBox();
  expect(toolbarBox).not.toBeNull();
  expect(toolbarBox!.height).toBeLessThanOrEqual(130);
  const horizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth
  );
  expect(horizontalOverflow).toBeLessThanOrEqual(2);
});

test("side navigation stays in the SPA and review cards paint independently", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("demo-mode-badge")).toBeVisible({ timeout: 30_000 });
  await page.evaluate(() => { (window as typeof window & { __fceSpaSentinel?: string }).__fceSpaSentinel = "alive"; });

  await page.getByRole("link", { name: /스카우트/ }).click();
  await expect(page).toHaveURL(/\/scout$/);
  await expect.poll(() => page.evaluate(() => (window as typeof window & { __fceSpaSentinel?: string }).__fceSpaSentinel)).toBe("alive");

  const startedAt = Date.now();
  await page.getByRole("link", { name: /복기 센터/ }).click();
  await expect(page.getByTestId("review-overview-page")).toBeVisible({ timeout: 1_000 });
  expect(Date.now() - startedAt).toBeLessThan(1_000);
  const reviewGrid = page.locator(".reviewOverviewGrid");
  await expect(reviewGrid.getByText("거래 복기", { exact: true })).toBeVisible();
  await expect(reviewGrid.getByText("엔진 상태", { exact: true })).toBeVisible();
  await expect(reviewGrid.getByText("계좌 성적표", { exact: true })).toBeVisible();
  await expect.poll(() => page.evaluate(() => (window as typeof window & { __fceSpaSentinel?: string }).__fceSpaSentinel)).toBe("alive");
});

test("all product routes keep production CSS and bounded controls", async ({ page }) => {
  const routes = [
    "/",
    "/scout",
    "/review",
    "/engine",
    "/performance",
    "/settings",
    "/trades"
  ];

  for (const route of routes) {
    await page.goto(route);
    const audit = await page.evaluate(() => {
      const root = document.documentElement;
      const oversizedControls = [...document.querySelectorAll("button, svg")]
        .map((element) => {
          const box = element.getBoundingClientRect();
          return {
            tag: element.tagName,
            className: element.getAttribute("class") ?? "",
            width: Math.round(box.width),
            height: Math.round(box.height)
          };
        })
        .filter((box) => {
          if (box.tag === "BUTTON") {
            return box.height > 120;
          }
          return (
            box.height > 520 ||
            (box.width > Math.max(480, window.innerWidth * 0.6) && box.height > 240)
          );
        });
      return {
        stylesheetCount: document.styleSheets.length,
        horizontalOverflow: root.scrollWidth - root.clientWidth,
        oversizedControls
      };
    });

    expect(audit.stylesheetCount, `${route}: production stylesheets`).toBeGreaterThanOrEqual(5);
    expect(audit.horizontalOverflow, `${route}: horizontal overflow`).toBeLessThanOrEqual(2);
    expect(audit.oversizedControls, `${route}: oversized button/svg`).toEqual([]);
  }
});

test("live position cockpit smoke path", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("demo-mode-badge")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("position-strip")).toBeVisible();
  await expect(page.getByTestId("minimal-asset-card")).toHaveCount(3);
  await expect(page.getByTestId("compact-chart-workspace")).toBeVisible();
  await expect(page.getByTestId("stance-ribbon")).toBeVisible();
  await expect(page.getByTestId("stance-hud")).toBeVisible();
  await expect(page.getByTestId("stance-hud")).toContainText("4h");
  await expect(page.getByTestId("stance-hud")).toContainText("상방");
  await expect(page.getByTestId("stance-hud")).toContainText("하방");
  await expect(page.getByTestId("chart-canvas-frame").getByTestId("stance-hud")).toHaveCount(0);
  await expect(page.getByTestId("chart-canvas-frame").getByTestId("stance-ribbon")).toHaveCount(0);
  expect(await page.locator("[data-stance-flip='true']").count()).toBeGreaterThan(0);
  expect(await page.locator("[data-compact-level-label]").count()).toBeLessThanOrEqual(3);
  await expect(page.getByTestId("position-market-context")).toBeVisible();
  await expect(page.getByTestId("direction-gauge")).toHaveCount(0);
  await expect(page.getByTestId("take-profit-gauge")).toBeVisible();
  const moneyFlow = page.getByTestId("money-flow-card");
  await expect(moneyFlow).toBeVisible();
  await expect(moneyFlow).toContainText("현물 체결");
  await expect(moneyFlow).toContainText("선물 체결");
  await expect(moneyFlow).toContainText("미결제약정");
  await expect(moneyFlow).toContainText("CVD = 실제 입금액이 아닌");
  await expect(moneyFlow.locator("polyline")).toHaveCount(0);
  await expect(page.getByTestId("compact-gauge-panel").getByTestId("money-flow-card")).toHaveCount(0);
  const chartFrameBox = await page.getByTestId("chart-canvas-frame").boundingBox();
  const moneyFlowBox = await moneyFlow.boundingBox();
  expect(chartFrameBox).not.toBeNull();
  expect(moneyFlowBox).not.toBeNull();
  expect(moneyFlowBox!.y).toBeGreaterThanOrEqual(chartFrameBox!.y + chartFrameBox!.height - 1);
  await expect(page.getByTestId("position-chart")).toBeVisible();
  await expect(page.getByTestId("chart-canvas-frame")).toBeVisible();
  await expect(page.getByRole("button", { name: "포지션 목록" })).toBeVisible();
  await expect(page.getByTestId("position-entry-line")).toHaveCount(1);
  await expect(page.getByTestId("position-entry-line")).toHaveAttribute("x2", /\d+/);
  await expect(page.locator("[data-testid^='chart-layer-']")).toHaveCount(0);

  const initialChartHeading = await page.getByTestId("position-chart").getByRole("heading").innerText();
  await page.getByTestId("minimal-asset-card").filter({ hasText: "ETHUSDT" }).click();
  await expect(page).toHaveURL(/position=/);
  await expect(page.locator("[data-event-pill]").first()).toBeVisible({ timeout: 30_000 });
  await page.goBack();
  await expect(page).not.toHaveURL(/position=/);
  await expect(page.getByTestId("position-chart").getByRole("heading")).toHaveText(initialChartHeading);
  await page.getByTestId("minimal-asset-card").filter({ hasText: "ETHUSDT" }).click();

  await page.getByTestId("pro-mode-button").click();
  await expect(page.getByTestId("verdict-bar")).toBeVisible();
  await expect(page.getByTestId("verdict-bar")).toContainText("건강도");
  await expect(page.getByTestId("verdict-bar")).not.toContainText(/\d[\d,]*\.\d{3,}/);
  await expect(page.getByTestId("action-plan")).toBeVisible();
  await expect(page.locator(".analysisChartColumn").getByTestId("money-flow-card")).toBeVisible();
  await expect(page.locator(".evidenceRoomRail").getByTestId("money-flow-card")).toHaveCount(0);
  await expect(page.getByTestId("realized-liquidation-heatmap")).toHaveCount(0);
  await expect(page.getByTestId("chart-layer-liquidation_realized")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("chart-layer-liquidation_estimated")).toBeDisabled();
  await expect(page.getByTestId("unified-heatmap-controls")).toContainText("과거 발생 · 예측 아님");
  await expect(page.getByTestId("unified-heatmap-controls")).toContainText("N=");
  await expect(page.getByTestId("unified-heatmap-live")).toContainText("LIVE");
  await expect(page.getByTestId("unified-heatmap-live")).toContainText("5초 갱신");
  await expect(page.getByTestId("unified-heatmap-live")).toContainText("최근 확정");
  await expect(page.getByTestId("unified-heatmap-zones")).toContainText("밝은 실선");
  await expect(page.getByTestId("unified-heatmap-zones")).toContainText(/밀집 1|아직 표시할 실현 이벤트 없음/);
  await expect(page.getByTestId("unified-heatmap-summary")).toHaveCount(0);
  await expect(page.getByTestId("unified-heatmap-canvas")).toBeVisible();
  await expect(page.getByRole("slider", { name: "청산 히트맵 투명도" })).toHaveValue("0.68");
  const heatmapControlsBox = await page.getByTestId("unified-heatmap-controls").boundingBox();
  const zoneGridBox = await page.locator(".unifiedHeatmapZoneGrid").boundingBox();
  expect(heatmapControlsBox).not.toBeNull();
  expect(zoneGridBox).not.toBeNull();
  expect(zoneGridBox!.x + zoneGridBox!.width).toBeLessThanOrEqual(heatmapControlsBox!.x + heatmapControlsBox!.width + 1);
  const zoneButtons = page.locator(".unifiedHeatmapZoneGrid > button");
  for (let index = 0; index < await zoneButtons.count(); index += 1) {
    const zoneBox = await zoneButtons.nth(index).boundingBox();
    expect(zoneBox).not.toBeNull();
    expect(zoneBox!.x + zoneBox!.width).toBeLessThanOrEqual(heatmapControlsBox!.x + heatmapControlsBox!.width + 1);
  }
  const beforeFilterUrl = page.url();
  const advancedFilters = page.getByTestId("unified-heatmap-advanced");
  await expect(advancedFilters.getByRole("group", { name: "방향" })).not.toBeVisible();
  await advancedFilters.locator("summary").click();
  await expect(advancedFilters.getByRole("group", { name: "방향" })).toBeVisible();
  await advancedFilters.getByRole("button", { name: "Q4" }).click();
  await expect(page.getByTestId("unified-heatmap-controls").getByRole("button", { name: "Q4" })).toHaveAttribute("aria-pressed", "true");
  await page.getByTestId("unified-heatmap-controls").getByRole("button", { name: "발생 시점", exact: true }).click();
  await expect(page.getByTestId("unified-heatmap-controls").getByRole("button", { name: "발생 시점", exact: true })).toHaveAttribute("aria-pressed", "true");
  expect(page.url()).toBe(beforeFilterUrl);
  const proPanelBox = await page.locator(".analysisChartColumn .positionChartPanel").boundingBox();
  const proFrameBox = await page.locator(".analysisChartColumn").getByTestId("chart-canvas-frame").boundingBox();
  expect(proPanelBox).not.toBeNull();
  expect(proFrameBox).not.toBeNull();
  expect(proPanelBox!.height - proFrameBox!.height).toBeLessThan(430);
  await expect(page.getByTestId("chart-layer-flow")).toHaveCount(0);
  await expect(page.getByTestId("chart-layer-indicators")).toHaveCount(0);
  await expect(page.getByTestId("chart-advanced-layers")).not.toHaveAttribute("open", "");
  await expect(page.locator(".chartLayerPrimary > button")).toHaveCount(6);
  await page.getByTestId("chart-advanced-layers").locator("summary").click();
  await page.getByTestId("chart-layer-ema").click();
  await expect(page.getByTestId("chart-layer-ema")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("ema-ribbon-hud")).toBeVisible();
  await expect(page.getByTestId("ema-ribbon-hud")).toContainText("EMA 20–55");
  await page.getByTestId("chart-layer-wyckoff").click();
  await expect(page.getByTestId("ema-ribbon-hud")).toBeVisible();
  await expect(page.getByTestId("chart-overlay")).toBeVisible();
  await page.getByTestId("chart-layer-liquidity").click();
  await expect(page.getByTestId("chart-layer-wyckoff")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("liquidity-layer")).toBeVisible();
  await page.getByTestId("chart-layer-ema").click();
  await page.getByTestId("chart-layer-wyckoff").click();
  await page.getByTestId("chart-layer-liquidity").click();
  await page.getByTestId("chart-layer-wyckoff").click({ modifiers: ["Shift"] });
  await expect(page.getByTestId("chart-compare-badge")).toBeVisible();
  await page.getByTestId("chart-layer-harmonic").click({ modifiers: ["Shift"] });
  await expect(page.getByTestId("chart-layer-harmonic")).toHaveAttribute("aria-pressed", "false");
  await page.getByRole("button", { name: "해설" }).click();
  await expect(page.getByTestId("chart-guide-layer")).toBeVisible();
  await page.getByRole("button", { name: "해설 켜짐" }).click();
  await expect(page.getByTestId("chart-guide-layer")).toHaveCount(0);
  await expect(page.getByTestId("evidence-room-panel")).toHaveAttribute("data-focus-layer", "wyckoff");
  await expect(page.getByTestId("chart-layer-plan")).toHaveAttribute("aria-pressed", "true");
});

test("minimal crypto chart renders confirmed whale long and short fills as triangles", async ({ page }) => {
  await page.route(/\/api\/live\/positions\/[^/]+\/chart-analysis\?/, async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    const candles = body.candles ?? [];
    expect(candles.length).toBeGreaterThanOrEqual(4);
    const reduced = candles.at(-4);
    const historical = candles.at(-3);
    const liveAnchor = candles.at(-2);
    const item = (side: "long" | "short", eventAt: string, price: number, event = "open") => ({
      id: `${side}-${event}-${eventAt}`,
      wallet_address: "0x1111111111111111111111111111111111111111",
      wallet_label: "E2E 고래",
      coin: body.symbol?.replace("USDT", "") ?? "BTC",
      symbol: body.symbol ?? "BTCUSDT",
      side,
      event,
      size_usd: 250_000,
      entry_px: price,
      mark_px: price,
      unrealized_pnl: 0,
      event_at: eventAt,
      validation_state: "candidate",
      sample_size: 4,
      win_1r_pct: null,
      accuracy_label: "적중률 축적 중 (N=4)",
      alias_disclaimer: "추정 별칭"
    });
    body.asset_class = "crypto";
    body.onchain = {
      supported: true,
      symbol: body.symbol,
      policy: "확정 체결 관측 전용",
      validated_evidence: [],
      markers: [
        {
          time: reduced.time,
          event_time: reduced.time + 60,
          kind: "exit",
          side: "long",
          event: "reduce",
          count: 1,
          size_usd: 180_000,
          size_tier: 1,
          price: reduced.close,
          label: "롱 감액",
          emphasized: false,
          live: false,
          items: [item("long", new Date((reduced.time + 60) * 1000).toISOString(), reduced.close, "reduce")]
        },
        {
          time: historical.time,
          event_time: historical.time + 60,
          kind: "entry",
          side: "long",
          event: "open",
          count: 1,
          size_usd: 250_000,
          size_tier: 2,
          price: historical.close,
          label: "롱 진입",
          emphasized: false,
          live: false,
          items: [item("long", new Date((historical.time + 60) * 1000).toISOString(), historical.close)]
        },
        {
          time: liveAnchor.time,
          event_time: liveAnchor.time + 14_400,
          kind: "entry",
          side: "short",
          event: "open",
          count: 1,
          size_usd: 250_000,
          size_tier: 2,
          price: liveAnchor.close,
          label: "숏 진입",
          emphasized: false,
          live: true,
          items: [item("short", new Date((liveAnchor.time + 14_400) * 1000).toISOString(), liveAnchor.close)]
        }
      ]
    };
    await route.fulfill({ response, json: body });
  });

  await page.goto("/");
  await expect(page.getByTestId("compact-chart-workspace")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("minimal-timeframe-selector")).toBeVisible();
  await expect(page.getByTestId("minimal-timeframe-4h")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("minimal-timeframe-selector")).toContainText("확정 캔들만 표시");
  const markers = page.locator('[data-testid="onchain-marker"][data-shape="triangle"]');
  await expect(markers).toHaveCount(3);
  await expect(markers.filter({ has: page.locator("path") }).first()).toBeVisible();
  await expect(page.locator('[data-testid="onchain-marker"][data-side="long"]')).toHaveCount(2);
  await expect(page.locator('[data-testid="onchain-marker"][data-side="short"][data-live="true"]')).toHaveCount(1);
  await expect(page.locator(".onchainMarkerLabel")).toContainText("실시간 숏+");
  await expect(page.locator('[data-testid="onchain-marker"][data-live="true"] title')).toContainText("체결");
  await expect(page.getByTestId("whale-marker-legend")).toContainText("+ 진입·증액");
  await expect(page.getByTestId("whale-marker-legend")).toContainText("− 감액·청산");
  const markerFills = await markers.locator("path:first-of-type").evaluateAll((paths) => paths.map((path) => path.getAttribute("fill")));
  expect(markerFills.every((fill) => Boolean(fill) && fill !== "none")).toBe(true);

  await markers.filter({ has: page.locator("title") }).first().click();
  await expect(page.getByTestId("whale-marker-details")).toContainText("E2E 고래");
  await expect(page.getByTestId("whale-marker-details")).toContainText("$180.00K");
  await expect(page.getByTestId("whale-marker-details")).toContainText("검증 중");

  const oneHourRequest = page.waitForRequest((request) => request.url().includes("chart-analysis") && request.url().includes("timeframe=1h"));
  await page.getByTestId("minimal-timeframe-1h").click();
  await oneHourRequest;
  await expect(page.getByTestId("minimal-timeframe-1h")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("position-chart")).toContainText("1시간봉");
});

test("unified liquidation density is the single live chart surface", async ({ page }) => {
  await page.route("**/api/live/positions/*/chart-analysis*", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    if (Array.isArray(body.candles)) {
      const lastConfirmed = Math.floor(Date.now() / 1000) - 4 * 60 * 60;
      body.candles = body.candles.map((candle: { time: number }, index: number) => ({
        ...candle,
        time: lastConfirmed - (body.candles.length - index - 1) * 4 * 60 * 60
      }));
    }
    await route.fulfill({ response, json: body });
  });

  await page.goto("/");
  await expect(page.getByTestId("demo-mode-badge")).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("minimal-asset-card").filter({ hasText: "ETHUSDT" }).click();
  await page.getByTestId("pro-mode-button").click();

  await expect(page.getByTestId("realized-liquidation-heatmap")).toHaveCount(0);
  await expect(page.getByTestId("unified-heatmap-controls")).toBeVisible();
  await expect(page.getByTestId("unified-heatmap-live")).toContainText("LIVE");
  await expect(page.getByTestId("unified-heatmap-live")).toContainText("최근 확정");
  await expect(page.getByTestId("unified-heatmap-zones")).toContainText("핵심 밀집 구간");
  await expect(page.getByTestId("unified-heatmap-canvas")).toBeVisible();
  await expect(page.locator("[data-price-flag-kind='mark'] .liveMarkPulse")).toHaveCount(1);

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByTestId("unified-heatmap-zones")).toBeVisible();
  const horizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth
  );
  expect(horizontalOverflow).toBeLessThanOrEqual(2);
});

test("money flow exposes the driver, magnitude, and execution history", async ({ page }) => {
  await page.route("**/api/live/positions/*/chart-analysis*", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    if (body.derivatives?.signals) {
      body.derivatives.signals.money_flow = {
        as_of: "2026-07-13T12:00:00Z",
        source: "bitget_spot",
        source_label: "Bitget 단일 거래소 프록시",
        spot_cvd_delta_ratio: 0.32,
        futures_cvd_delta_ratio: 0.08,
        price_change_pct: 2.8,
        oi_change_pct: 1.4,
        spot_cvd: Array.from({ length: 20 }, (_, index) => ({ time: index, value: index * 14 + (index % 4 === 0 ? -8 : 0) })),
        futures_cvd: Array.from({ length: 20 }, (_, index) => ({ time: index, value: index * 4 + (index % 3 === 0 ? -7 : 0) })),
        coverage: { spot_available: true, futures_available: true },
        notes: [],
        state: "spot_led",
        label: "현물 유입 동반 상승",
        available: true,
        provisional: false,
        sample_size: 34,
        required_samples: 10,
        confidence: 78,
        directions: { price: "up", spot_cvd: "up", futures_cvd: "up", oi: "up" },
        reason: "최근 30일 관측 분포의 40백분위로 방향을 구분했습니다."
      };
      body.derivatives.signals.etf_flow = {
        asset: "BTC",
        available: true,
        status: "ok",
        source: "coinglass_v4",
        source_label: "CoinGlass 미국 현물 ETF 집계",
        as_of: "2026-07-21T00:00:00Z",
        daily_flow_usd: 125000000,
        five_report_day_flow_usd: -45000000,
        report_days: 5,
        contributors: [
          { ticker: "IBIT", flow_usd: 150000000 },
          { ticker: "GBTC", flow_usd: -25000000 }
        ],
        cadence: "daily",
        truth_label: "일별 ETF 보고 · 실시간 체결 아님"
      };
    }
    body.options = {
      available: true,
      source: "occ_public",
      source_label: "OCC 공식 무키 데이터",
      underlying: "SOXL",
      call_open_interest: 434745,
      put_open_interest: 800996,
      put_call_oi_ratio: 1.8425,
      call_volume: 123628,
      put_volume: 257380,
      put_call_volume_ratio: 2.0819,
      volume_date: "2026-07-14",
      max_pain_price: 31.5,
      max_pain_expiry: "2026-07-17",
      days_to_expiry: 2,
      max_pain_basis: "nearest_expiry_open_interest"
    };
    await route.fulfill({ response, json: body });
  });

  await page.goto("/");
  const moneyFlow = page.getByTestId("money-flow-card");
  await expect(moneyFlow).toBeVisible({ timeout: 30_000 });
  await expect(moneyFlow).toContainText("현물 주도 상승");
  await expect(moneyFlow).toContainText("현물 매수 체결이 가격 상승을 지지");
  await expect(moneyFlow).toContainText("판정 신뢰 78%");
  await expect(moneyFlow).toContainText("+32.0%");
  await expect(moneyFlow).toContainText("+8.00%");
  await expect(moneyFlow.locator(".flowHistogramPlot")).toHaveCount(2);
  await expect(moneyFlow.locator("polyline")).toHaveCount(0);
  const etfFlow = page.getByTestId("crypto-etf-flow-summary");
  await expect(etfFlow).toBeVisible();
  await expect(etfFlow).toContainText("BTC 현물 ETF");
  await expect(etfFlow).toContainText("+$125M");
  await expect(etfFlow).toContainText("−$45M");
  await expect(etfFlow).toContainText("IBIT +$150M");
  await expect(etfFlow).toContainText("일별 ETF 보고 · 실시간 체결 아님");
  const putCall = page.getByTestId("options-put-call-summary");
  await expect(putCall).toBeVisible();
  await expect(putCall).toContainText("풋/콜 비율");
  await expect(putCall).toContainText("1.84");
  await expect(putCall).toContainText("2.08");
  await expect(putCall).toContainText("최근접 만기 맥스페인");
  await expect(putCall).toContainText("$31.50");
  await expect(putCall).toContainText("2026-07-17 · D-2");

  await page.setViewportSize({ width: 390, height: 1200 });
  await expect(moneyFlow).toBeVisible();
  await expect(etfFlow).toBeVisible();
  const horizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(horizontalOverflow).toBeLessThanOrEqual(0);
});

test("RWA money flow distinguishes unsupported spot CVD from live futures CVD", async ({ page }) => {
  await page.route("**/api/live/positions/*/chart-analysis*", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    if (body.derivatives?.signals) {
      body.derivatives.signals.money_flow = {
        as_of: "2026-07-15T20:00:00Z",
        source: "bitget_spot",
        source_label: "Bitget 단일 거래소 프록시",
        spot_cvd_delta_ratio: null,
        futures_cvd_delta_ratio: -0.022,
        price_change_pct: -15.8,
        oi_change_pct: -4.62,
        spot_cvd: [],
        futures_cvd: Array.from({ length: 24 }, (_, index) => ({ time: index, value: index % 3 === 0 ? -index * 2 : index })),
        coverage: {
          spot_available: false,
          futures_available: true,
          spot_mapping: "mapping_unavailable",
          futures_cvd_method: "event_time_fills"
        },
        notes: ["Bitget 현물 마켓 매핑 또는 체결 데이터를 확인할 수 없습니다."],
        state: "mixed",
        label: "자금 흐름 판정 불가",
        available: false,
        provisional: false,
        sample_size: 0,
        reason: "Bitget 현물 마켓 매핑 또는 체결 데이터를 확인할 수 없습니다."
      };
    }
    await route.fulfill({ response, json: body });
  });

  await page.goto("/");
  const moneyFlow = page.getByTestId("money-flow-card");
  await expect(moneyFlow).toContainText("선물 체결만 제공", { timeout: 30_000 });
  await expect(moneyFlow).toContainText("현물 마켓 미지원");
  await expect(moneyFlow).toContainText("최근 실제 체결 24구간");
  await expect(moneyFlow.getByText("구간 시계열 축적 중")).toHaveCount(0);
  await expect(moneyFlow.locator(".flowHistogramPlot")).toHaveCount(1);
});

test("minimal evidence deep-links into a reproducible evidence room", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("compact-chart-workspace")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "프로에서 검증" }).click();
  await expect(page).toHaveURL(/mode=pro/);
  await expect(page).toHaveURL(/focus=levels/);
  await expect(page.getByTestId("evidence-room-panel")).toHaveAttribute("data-focus-layer", "levels");
  await page.reload();
  await expect(page.getByTestId("pro-mode-button")).toHaveClass(/active/);
  await expect(page.getByTestId("chart-layer-levels")).toHaveAttribute("aria-pressed", "true");
});

test("stock perpetual position shows Toss source hierarchy instead of crypto whales", async ({ page, request }) => {
  const mappingState = {
    targets: [{
      symbol: "SOXLUSDT",
      sources: ["position"],
      asset_class: "stock",
      source_category: "bitget_rwa",
      join_eligible: true,
      join_reason: "검증 대상",
      mapping_status: "verified"
    }],
    items: [{
      bitget_symbol: "SOXLUSDT",
      bitget_type: "usdt_futures",
      underlying_name: "DIREXION DAILY SEMICONDUCTOR BULL 3X SHARES",
      underlying_kind: "leveraged_etf",
      toss_symbol: "SOXL",
      toss_market: "US",
      toss_exchange: "AMEX",
      leverage_note: "3x 레버리지 ETF · Bitget 퍼페추얼 결합 시 레버리지 중첩",
      verification_status: "verified",
      verified_by: "manual",
      verified_at: "2026-07-18T13:00:00Z",
      identity_match: true,
      notes: "사용자 수동 승인",
      verification_evidence: {
        checks: { official_name: true, exchange: true, asset_type: true },
        ticker_only_match_used: false
      },
      created_at: "2026-07-18T12:00:00Z",
      updated_at: "2026-07-18T13:00:00Z"
    }],
    policy: {
      price_of_record: "Bitget",
      structure_source: "Toss underlying",
      pending_join_enabled: false,
      crypto_toss_enabled: false
    }
  };

  await page.route("**/api/instrument-maps**", async (route) => {
    await route.fulfill({ json: mappingState });
  });

  const livePositionsResponse = await request.get("/api/live/positions");
  expect(livePositionsResponse.ok()).toBe(true);
  const livePositionsPayload = await livePositionsResponse.json();
  const sourcePosition = livePositionsPayload.positions[0];
  expect(sourcePosition?.position?.id).toBeTruthy();
  sourcePosition.position.symbol = "SOXLUSDT";

  const positionId = sourcePosition.position.id;
  const detailResponse = await request.get(`/api/live/positions/${positionId}`);
  const detailPayload = await detailResponse.json();
  detailPayload.position.symbol = "SOXLUSDT";

  const chartResponse = await request.get(`/api/live/positions/${positionId}/chart-analysis?timeframe=4h&compact=true`);
  const chartPayload = await chartResponse.json();
  chartPayload.symbol = "SOXLUSDT";
  chartPayload.asset_class = "stock";
  chartPayload.underlying_join = {
    status: "joined",
    price_of_record: "bitget",
    structure_source: "toss",
    structure_timeframe: "1d",
    bitget_symbol: "SOXLUSDT",
    bitget_price: 133.5,
    toss_symbol: "SOXL",
    toss_price: 134.03,
    toss_price_at: "2026-07-18T08:59:00Z",
    basis_pct: -0.4,
    market_state: "closed",
    stale: true,
    underlying_name: "DIREXION DAILY SEMICONDUCTOR BULL 3X SHARES",
    underlying_kind: "leveraged_etf",
    toss_exchange: "AMEX",
    leverage_note: "3x 레버리지 ETF · Bitget 퍼페추얼 결합 시 레버리지 중첩",
    flow_status: "unavailable_us",
    flow_note: "Toss US 투자자별 수급 미제공 · 해당 신호 비활성",
    warning_gate_blocked: false,
    warning_badges: []
  };
  const deepDivePayload = {
    status: "ready",
    position_id: positionId,
    symbol: "SOXLUSDT",
    as_of: "2026-07-18T13:20:00Z",
    truth_policy: "서로 다른 출처에서 동시에 관측된 값만 교차신호로 표시하며, 주문 판단에는 사용하지 않습니다.",
    underlying: { symbol: "SOXL", name: "DIREXION DAILY SEMICONDUCTOR BULL 3X SHARES", exchange: "AMEX", kind: "leveraged_etf", market_state: "closed", stale: true },
    entry_snapshot: { capture_policy: "first_observed_proxy", thesis: { text: "반도체 기초 구조 회복 관측", source: "user" }, structure: { overall_stance: "하방" } },
    thesis: { status: "weakened", status_label: "약화", text: "반도체 기초 구조 회복 관측", source: "user", entry: {}, current: {}, comparison_note: "최초 심화 관측 스냅샷과 비교합니다." },
    cross_signals: [
      { id: "basis_behavior", label: "베이시스 행동", status: "active", sources: [{ id: "bitget", label: "Bitget" }, { id: "toss", label: "Toss" }], moat_reason: "두 가격이 모두 필요합니다.", reading: "괴리폭 축소 관측", detail: "현재 -0.40% · 괴리폭 -0.40%p", data: { state: "contracting", sparkline: [{ time: 1, value: -0.8, kind: "confirmed_close" }, { time: 2, value: -0.4, kind: "live_observation" }] } },
      { id: "funding_momentum_divergence", label: "펀딩 × 기초 모멘텀", status: "active", sources: [{ id: "bitget", label: "Bitget" }, { id: "toss", label: "Toss" }], moat_reason: "펀딩과 기초 일봉이 모두 필요합니다.", reading: "쏠림과 기초 모멘텀 불일치 관측", detail: "펀딩 +0.0100% · 기초 5일 -2.10%", data: { state: "divergent" } },
      { id: "liquidation_shelf", label: "청산 선반 상대 위치", status: "active", sources: [{ id: "bitget", label: "Bitget" }, { id: "position", label: "내 포지션" }], moat_reason: "청산대와 진입가가 모두 필요합니다.", reading: "진입가는 청산 선반 위", detail: "선반 120.00 · 실현 추정 명목 $250,000", data: { state: "위" } },
      { id: "underlying_flow_alignment", label: "기초 수급 × 내 방향", status: "unavailable", sources: [{ id: "toss", label: "Toss" }, { id: "position", label: "내 포지션" }], moat_reason: "수급과 방향이 모두 필요합니다.", reading: "데이터 없음", detail: "기초자산 시장 비거래 시간 · 수급 신호 비활성", data: { reason: "market_closed" } },
      { id: "leverage_stack", label: "레버리지 중첩", status: "active", sources: [{ id: "toss", label: "Toss" }, { id: "bitget", label: "Bitget" }, { id: "position", label: "내 포지션" }], moat_reason: "세 배율이 모두 필요합니다.", reading: "중첩 익스포저 관측", detail: "기초 3x × 퍼페추얼 10x = 명목 30x", data: { state: "stacked", warning: "레버리지 ETF는 보유기간과 경로에 따라 기초지수 누적수익률과 괴리가 생길 수 있습니다." } }
    ],
    risk: {
      liquidation_distance_pct: 31.2,
      invalidation_price: 116.7,
      invalidation_distance_pct: 6.1,
      next_structure_price: 138.4,
      reward_risk_r: 1.42,
      market_reading: { stance: "down", label: "하방 관측", position_alignment: "opposed", reasons: ["레벨: 저항 아래 정체", "쏠림과 기초 모멘텀 불일치 관측"], reversal_condition: { price: 138.4, condition: "확정 캔들이 상단 구조를 회복하면 현재 하방 읽기를 재평가", source: "Bitget 확정 캔들 구조 레벨" } },
      partial_exit_simulation: [{ reduction_pct: 25, remaining_quantity: 3.75, remaining_notional: 500.63, liquidation_distance_pct: 31.2, invalidation_risk_notional: 30.54, assumption: "정적 계산" }]
    },
    ledger: { latest_judgment_id: "j1", outcomes: [], performance: [{ horizon_days: 1, n: 0, hit_rate_pct: null, sample_low: true }, { horizon_days: 5, n: 0, hit_rate_pct: null, sample_low: true }, { horizon_days: 20, n: 0, hit_rate_pct: null, sample_low: true }], signal_performance: [{ signal_id: "basis_behavior", signal_label: "베이시스 행동", horizon_days: 1, n: 0, hit_rate_pct: null, sample_low: true }], horizons: [1, 5, 20], score_policy: "실제 경과 가격만 기록합니다." }
  };

  await page.route("**/api/live/positions**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/chart-analysis")) {
      await route.fulfill({ json: chartPayload });
      return;
    }
    if (url.pathname === `/api/live/positions/${positionId}/deepdive`) {
      await route.fulfill({ json: deepDivePayload });
      return;
    }
    if (url.pathname === `/api/live/positions/${positionId}`) {
      await route.fulfill({ json: detailPayload });
      return;
    }
    if (url.pathname === "/api/live/positions") {
      await route.fulfill({ json: livePositionsPayload });
      return;
    }
    await route.continue();
  });

  await page.goto("/");
  const sourceBanner = page.getByTestId("position-underlying-banner");
  await expect(sourceBanner).toBeVisible({ timeout: 30_000 });
  await expect(sourceBanner).toContainText("Bitget 실행 · Toss 구조");
  await expect(sourceBanner).toContainText(/기초자산 장중|기초자산 장 마감/);
  await expect(sourceBanner).not.toContainText("Bitget 실행가");
  await expect(page.getByTestId("position-whale-banner")).toHaveCount(0);
  await expect(page.locator('[data-testid="onchain-marker"]')).toHaveCount(0);
  await expect(page.getByTestId("underlying-join-strip")).toBeVisible();
  await expect(page.getByTestId("underlying-join-strip")).toContainText("Toss 원본");
  await expect(page.getByTestId("position-deepdive-panel")).toBeVisible();
  await expect(page.getByTestId("deepdive-thesis-block")).toContainText("약화");
  await expect(page.getByTestId("deepdive-cross-signal-block")).toContainText("명목 30x");
  await expect(page.getByTestId("deepdive-cross-signal-block")).toContainText("수급 신호 비활성");
  await expect(page.getByTestId("deepdive-risk-ledger-block")).toContainText("표본 부족");
  await expect(page.getByTestId("deepdive-signal-performance")).toContainText("베이시스 행동");

  await page.setViewportSize({ width: 390, height: 844 });
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth
  );
  expect(overflow).toBeLessThanOrEqual(2);
});

test("scout and analysis smoke paths", async ({ page }) => {
  await page.goto("/scout");
  await expect(page.getByTestId("demo-mode-badge")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scout-page")).toBeVisible();
  await page.getByRole("button", { name: "주식 KR", exact: true }).click();
  await expect(page.getByTestId("stock-scout-page")).toBeVisible();
  await expect(page.getByTestId("stock-scout-page")).toContainText("Toss 데이터 · 주문 실행 없음");
  await expect(page.getByTestId("stock-scout-page")).toContainText("근거 없는 종목과 숫자는 생성하지 않습니다.");
  await page.getByRole("button", { name: "판정 성적", exact: true }).click();
  await expect(page.getByTestId("stock-scout-page")).toContainText("T+1 · T+5 · T+20");
  await page.getByRole("button", { name: "크립토", exact: true }).click();
  await expect(page.getByTestId("scout-page")).toBeVisible();
  if (await page.getByTestId("catalog-status-banner").isVisible().catch(() => false)) {
    await page.getByTestId("catalog-status-banner").getByRole("button", { name: "재시도" }).click();
    await expect(page.getByTestId("catalog-status-banner")).toBeHidden({ timeout: 30_000 });
  }
  await page.getByPlaceholder("심볼 검색 (예: BTC, SOL) — 추가하면 관심종목에 담깁니다").fill("BTC");
  await expect(page.getByTestId("scout-quick-answer")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("compact-chart-workspace")).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("scout-quick-answer").getByRole("button", { name: "자세히", exact: true }).click();
  await expect(page.getByTestId("scout-analysis-view")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("compact-chart-workspace")).toBeVisible();
  await expect(page.getByTestId("stance-ribbon")).toBeVisible();
  await expect(page.getByTestId("position-market-context")).toHaveCount(0);
  await expect(page.getByTestId("direction-gauge")).toHaveCount(0);
  await expect(page.getByTestId("take-profit-gauge")).toHaveClass(/inactive/);

});

test("instrument join requires explicit approval and supports rejection", async ({ page }) => {
  const statuses: Record<string, "pending" | "verified" | "rejected"> = {
    NBISUSDT: "pending",
    SOXLUSDT: "pending"
  };
  const item = (symbol: string) => ({
    bitget_symbol: symbol,
    bitget_type: "usdt_futures",
    underlying_name: symbol === "NBISUSDT" ? "NEBIUS GROUP N.V." : "DIREXION DAILY SEMICONDUCTOR BULL 3X SHARES",
    underlying_kind: symbol === "NBISUSDT" ? "stock" : "leveraged_etf",
    toss_symbol: symbol.replace("USDT", ""),
    toss_market: "US",
    toss_exchange: symbol === "NBISUSDT" ? "NASDAQ" : "AMEX",
    leverage_note: symbol === "SOXLUSDT" ? "3배 레버리지 ETF · 배수 조정 없음" : null,
    verification_status: statuses[symbol],
    verified_by: statuses[symbol] === "pending" ? "auto-candidate" : "manual",
    verified_at: statuses[symbol] === "verified" ? "2026-07-18T13:00:00Z" : null,
    identity_match: true,
    notes: statuses[symbol] === "pending" ? "사용자 승인 대기" : "사용자 수동 결정",
    verification_evidence: {
      bitget: { underlying_name: symbol === "NBISUSDT" ? "NEBIUS GROUP N.V." : "DIREXION DAILY SEMICONDUCTOR BULL 3X SHARES", exchange: symbol === "NBISUSDT" ? "NASDAQ" : "AMEX", asset_type: symbol === "NBISUSDT" ? "stock" : "leveraged_etf" },
      toss: { official_name: symbol === "NBISUSDT" ? "NEBIUS GROUP N.V." : "DIREXION DAILY SEMICONDUCTOR BULL 3X SHARES", exchange: symbol === "NBISUSDT" ? "NASDAQ" : "AMEX", asset_type: symbol === "NBISUSDT" ? "stock" : "leveraged_etf" },
      checks: { official_name: true, exchange: true, asset_type: true },
      ticker_only_match_used: false
    },
    created_at: "2026-07-18T12:00:00Z",
    updated_at: "2026-07-18T13:00:00Z"
  });
  const state = () => ({
    targets: Object.keys(statuses).map((symbol) => ({
      symbol,
      sources: ["position"],
      asset_class: "stock",
      source_category: "bitget_rwa",
      join_eligible: true,
      join_reason: "검증 대상",
      mapping_status: statuses[symbol]
    })),
    items: Object.keys(statuses).map(item),
    policy: { price_of_record: "Bitget", structure_source: "Toss underlying", pending_join_enabled: false, crypto_toss_enabled: false }
  });

  await page.route("**/api/instrument-maps**", async (route) => {
    const url = new URL(route.request().url());
    const decision = url.pathname.match(/\/api\/instrument-maps\/(.+)\/(approve|reject)$/);
    if (decision) {
      const symbol = decodeURIComponent(decision[1]);
      statuses[symbol] = decision[2] === "approve" ? "verified" : "rejected";
      await route.fulfill({ json: { item: item(symbol) } });
      return;
    }
    await route.fulfill({ json: state() });
  });

  await page.goto("/scout");
  const panel = page.getByTestId("instrument-map-panel");
  await expect(panel).toBeVisible({ timeout: 30_000 });
  const nbis = panel.locator('[data-symbol="NBISUSDT"]');
  const soxl = panel.locator('[data-symbol="SOXLUSDT"]');
  await expect(nbis).toHaveAttribute("data-map-status", "pending");
  await expect(soxl).toContainText("3배 레버리지 ETF");
  await nbis.getByRole("button", { name: "승인", exact: true }).click();
  await expect(nbis).toHaveAttribute("data-map-status", "verified");
  await soxl.getByRole("button", { name: "거부", exact: true }).click();
  await expect(soxl).toHaveAttribute("data-map-status", "rejected");
});

test("manual scout tracking is one click and stays separate from engine detections", async ({ page }) => {
  await page.goto("/scout");
  await expect(page.getByTestId("demo-mode-badge")).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("scout-search-input").fill("SOXL");
  await page.getByRole("button", { name: "SOXLUSDT 추적 시작", exact: true }).click();

  const manualCard = page.locator('[data-tracking-source="manual"][data-symbol="SOXLUSDT"]');
  // CI 콜드 부팅(uvicorn+SQLite)에서 추적 반영이 10초를 넘길 수 있다 — 데모 배지와 같은 30초 상한.
  await expect(manualCard).toBeVisible({ timeout: 30_000 });
  await expect(manualCard).toContainText("내가 선택");
  await expect(manualCard).toContainText("수동 선택");

  await manualCard.getByRole("button", { name: "추적 해제", exact: true }).click();
  await expect(manualCard).toHaveCount(0);
});

test("engine trading workspace contains the absorbed calibration surface", async ({ page }) => {
  await page.goto("/engine");
  await expect(page.getByTestId("engine-trading-page")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("engine-battle-tab")).toBeVisible();
  await page.getByRole("link", { name: "엔진 포지션" }).click();
  await expect(page).toHaveURL(/tab=positions/);
  await expect(page.getByTestId("paper-validation-slots")).toBeVisible();
  await expect(page.getByTestId("paper-validation-slots")).toContainText(/4주 검증 슬롯/);
  await expect(page.getByTestId("paper-validation-slots")).toContainText(/실주문 없음/);
  await page.getByRole("link", { name: "거래 일지" }).click();
  await expect(page.getByTestId("engine-journal-tab")).toBeVisible();
  await page.getByRole("link", { name: "엔진 상태" }).click();
  await expect(page.getByTestId("engine-status-tab")).toBeVisible();
  await expect(page.getByTestId("real-history-stance-backtest")).toBeVisible();
  await expect(page.getByTestId("real-history-stance-backtest")).toContainText("합성 성적과 분리");
  await expect(page.getByTestId("real-history-stance-backtest")).toContainText("합성 80.8%와 합산 안 함");
  await expect(page.getByTestId("paper-gate-funnel")).toBeVisible();
  await expect(page.getByTestId("event-pill-diagnostics")).toBeVisible();

  await page.goto("/engine?tab=status");
  await expect(page.getByTestId("engine-status-tab")).toBeVisible();
});

test("stock paper tracks stay separate, sealed, and responsive", async ({ page }) => {
  const fill = {
    id: "fill-aapl-entry",
    order_id: "order-aapl-entry",
    market: "US" as const,
    symbol: "AAPL",
    side: "buy" as const,
    currency: "USD" as const,
    quantity: 3,
    price: 205.25,
    gross_amount: 615.75,
    commission: 0.15,
    transaction_tax: 0,
    filled_at: "2026-07-20T14:31:20Z",
    fx_rate_to_krw: null,
    fx_observed_at: null
  };
  const exitFill = {
    ...fill,
    id: "fill-aapl-exit",
    order_id: "order-aapl-exit",
    side: "sell" as const,
    quantity: 1,
    price: 205.8,
    gross_amount: 205.8,
    filled_at: "2026-07-20T14:32:10Z"
  };
  await page.route("**/api/stock-paper/dashboard", async (route) => {
    const track = (market: "KR" | "US") => ({
      market,
      currency: market === "KR" ? "KRW" : "USD",
      benchmark_index: market === "KR" ? "KOSPI100" : "NASDAQ100",
      benchmark_proxy_symbol: market === "KR" ? "237350" : "QQQ",
      benchmark_method: "unlevered_etf_proxy_close",
      universe_version: "2026-q3",
      started_at: "2026-07-19T00:00:00Z",
      ends_at: "2026-08-16T00:00:00Z",
      initial_cash: market === "KR" ? 100_000_000 : 100_000,
      cash: market === "KR" ? 99_950_000 : 99_950,
      status: "running",
      stop_reason: null,
      elapsed_days: 1,
      engine_return_pct: -0.05,
      nav: market === "KR" ? 99_950_000 : 99_950,
      nav_complete: true,
      benchmark_return_pct: 0.12,
      benchmark_observed_at: "2026-07-19T01:00:00Z",
      rejection_reasons: market === "KR" ? { session_closed: 2, price_limit_locked: 1 } : { liquidity_partial: 3 }
    });
    await route.fulfill({ json: {
      enabled: true,
      ready_to_start: true,
      start_block_reason: null,
      execution_model_complete: true,
      parameter_version: "stock-v1",
      as_of: "2026-07-19T01:00:00Z",
      tracks: [track("KR"), track("US")],
      positions: [],
      recent_fills: [exitFill, fill],
      fill_count: 2,
      live_orders_enabled: false,
      performance_gate: "Toss 실주문은 주식 페이퍼가 4주간 벤치마크를 초과할 경우에만 재논의",
      sample_note: "KR/US 원통화 성적이며 크립토 검증과 합산하지 않습니다.",
      universe: {
        version: "2026-q3",
        effective_at: "2026-07-01",
        total: 200,
        markets: { KR: 100, US: 100 },
        sources: {},
        refresh_policy: "quarterly_manual"
      }
    }});
  });
  await page.route("**/api/stock-paper/entry-chart?**", async (route) => {
    await route.fulfill({ json: {
      market: "US",
      symbol: "AAPL",
      timeframe: "1m",
      source: "toss",
      candles: [29, 30, 31, 32].map((minute) => ({
        opened_at: `2026-07-20T14:${minute}:00Z`,
        open: 204 + minute / 100,
        high: 206,
        low: 203,
        close: 205 + minute / 100,
        volume: 1_000,
        source: "toss",
        observed_at: "2026-07-20T14:33:00Z"
      })),
      fills: [fill, exitFill],
      empty_reason: null
    }});
  });

  await page.goto("/engine?tab=stocks");
  const view = page.getByTestId("engine-stock-paper-tab");
  await expect(view).toBeVisible({ timeout: 30_000 });
  await expect(view).toContainText("실주문 영구 봉인");
  await expect(view).toContainText("200종목");
  await expect(view).toContainText("크립토 성적과 합산하지 않습니다");
  await expect(view).toContainText("미체결 사유 분포");
  await expect(page.getByTestId("stock-paper-entry-chart")).toContainText("언제 진입했나");
  await expect(page.getByTestId("stock-paper-entry-chart")).toContainText("2026. 07. 20.");
  await expect(page.getByTestId("stock-paper-entry-chart")).toContainText("청산");
  await expect(page.getByTestId("stock-paper-entry-chart").locator(".stockEntryChartCanvas")).toBeVisible();
  await expect(view.locator(".stockTrackCard")).toHaveCount(2);
  if (process.env.FCE_CAPTURE_WO_SCREENSHOT === "true") {
    await page.screenshot({ path: "../docs/assets/WO-FCE-TOSS-PAPER-01-dashboard.png", fullPage: true });
  }

  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(
    () => page.evaluate(() => ({
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      clipped: [...document.querySelectorAll("[data-testid='engine-stock-paper-tab'] *")]
        .filter((element) => element.scrollWidth > element.clientWidth + 2).length
    })),
    { message: "responsive chart reflow should finish without page overflow or clipped controls" }
  ).toEqual({ overflow: 0, clipped: 0 });
});

test("engine core remains usable when optional whale data fails", async ({ page }) => {
  await page.route("**/api/onchain/whales", async (route) => {
    await route.abort("failed");
  });

  await page.goto("/engine");

  await expect(page.getByTestId("engine-battle-tab")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/고래 관측 갱신 실패/)).toBeVisible();
  await expect(page.getByText(/페이퍼 엔진 화면은 계속 사용할 수 있습니다/)).toBeVisible();
});

test("Polymarket paper tab exposes attributed probability and sample honesty", async ({ page }) => {
  const now = "2026-07-22T03:00:00Z";
  await page.route("**/api/poly-paper/dashboard", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      enabled: true,
      parameter_version: "poly-v1",
      read_only_label: "Public market data · PaperBroker only · 지갑/실주문 없음",
      performance_gate: "대표 산출물은 수익률이 아니라 만기 Brier score와 calibration입니다.",
      sample_note: "N<30에서는 캘리브레이션 품질 판정을 유보합니다.",
      categories: ["crypto", "macro"],
      live_orders_enabled: false,
      track: { currency: "USDC", cash: 9850, initial_cash: 10000, clock_valid: 1, elapsed_days: 1, status: "running", last_collection_at: now },
      markets: [{
        market_id: "btc-100k", slug: "btc-100k", question: "Will Bitcoin be above $100,000?", category: "crypto",
        observed_at: now, end_at: "2026-07-30T00:00:00Z", active: 1, closed: 0, market_probability: 0.52,
        liquidity: 250000, trade_eligible: 1, exclusion_reason: null,
        metadata: { resolution_source: "official", source: "polymarket_gamma_public" },
        estimate: {
          id: "estimate-1", market_id: "btc-100k", observed_at: now, market_probability: 0.52,
          estimated_probability: 0.7, confidence_band: [0.62, 0.78], estimate_quality: "high",
          base_rate: { model: "lognormal_zero_drift_v1" },
          evidence: [{ claim: "BTC 현물 108,000", source: "bitget:4h", observed_at: now, value: 108000 }],
          reasoning: "현물·확정 4시간봉 실현 변동성·남은 시간을 적용한 관측 확률입니다.",
          direction: "YES", gross_edge: 0.18, effective_price: 0.54, after_cost_edge: 0.16,
          trade_eligible: true, exclusion_reason: null, entity_type: "polymarket"
        }
      }],
      positions: [], recent_fills: [], resolution_count: 1,
      calibration: { n: 1, mean_brier_score: 0.09, sample_sufficient: false, sample_warning: "표본 부족 · N=1/30", curve: Array.from({ length: 10 }, (_, index) => ({ bucket: `${index * 10}–${(index + 1) * 10}%`, n: index === 7 ? 1 : 0, mean_forecast: index === 7 ? 0.7 : null, actual_yes_rate: index === 7 ? 1 : null })) }
    }) });
  });

  await page.goto("/engine?tab=polymarket");
  const view = page.getByTestId("engine-poly-paper-tab");
  await expect(view).toBeVisible({ timeout: 30_000 });
  await expect(view).toContainText("지갑·실주문 구현 없음");
  await expect(view).toContainText("Brier 0.0900");
  await expect(view).toContainText("표본 부족 · N=1/30");
  await expect(view).toContainText("비용 후 edge");
  await view.getByText("근거·베이스레이트 보기").click();
  await expect(view).toContainText("bitget:4h");
});

test("onchain flow keeps flip meaning and filters balanced events by instrument", async ({ page }) => {
  await page.route("**/api/onchain/whales", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    const now = new Date().toISOString();
    const sndk = {
      ...body.flow,
      instrument: "XYZ:SNDK",
      current_long_usd: 0,
      current_short_usd: 707_000,
      current_net_usd: -707_000,
      flow_24h_usd: -707_000,
      event_count_24h: 4,
      symbols: [{ symbol: "XYZ:SNDK", long_usd: 0, short_usd: 707_000, net_usd: -707_000, wallet_count: 1, event_count_24h: 4 }]
    };
    const btc = {
      ...body.flow,
      instrument: "BTCUSDT",
      current_long_usd: 0,
      current_short_usd: 11_000_000,
      current_net_usd: -11_000_000,
      flow_24h_usd: -66_000,
      event_count_24h: 2,
      symbols: [{ symbol: "BTCUSDT", long_usd: 0, short_usd: 11_000_000, net_usd: -11_000_000, wallet_count: 1, event_count_24h: 2 }]
    };
    body.flow.symbols = [...sndk.symbols, ...btc.symbols];
    body.flow_by_instrument = { "XYZ:SNDK": sndk, BTCUSDT: btc };
    body.recent_events = [
      {
        id: "burst:sndk",
        wallet_address: "0x1111111111111111111111111111111111111111",
        wallet_label: "리더보드 고래 #25",
        coin: "XYZ:SNDK",
        symbol: "",
        instrument: "XYZ:SNDK",
        side: "short",
        event: "increase",
        action_label: "숏 증액",
        size_usd: 707_000,
        entry_px: 210,
        mark_px: null,
        unrealized_pnl: null,
        event_at: now,
        fill_count: 4,
        raw_event_ids: ["1", "2", "3", "4"],
        validation_state: "candidate",
        trust_status: "validating",
        sample_size: 0,
        win_1r_pct: null,
        accuracy_label: "적중률 축적 중 (N=0)",
        alias_disclaimer: "공개 계정"
      },
      {
        id: "burst:btc",
        wallet_address: "0x2222222222222222222222222222222222222222",
        wallet_label: "리더보드 고래 #151",
        coin: "BTC",
        symbol: "BTCUSDT",
        instrument: "BTCUSDT",
        side: "short",
        event: "flip",
        action_label: "롱→숏 전환",
        size_usd: 208_000,
        entry_px: 65_651,
        mark_px: null,
        unrealized_pnl: null,
        event_at: now,
        fill_count: 1,
        raw_event_ids: ["5"],
        validation_state: "candidate",
        trust_status: "validating",
        sample_size: 0,
        win_1r_pct: null,
        accuracy_label: "적중률 축적 중 (N=0)",
        alias_disclaimer: "공개 계정"
      }
    ];
    body.recent_events_by_instrument = {
      "XYZ:SNDK": [body.recent_events[0]],
      BTCUSDT: [body.recent_events[1]],
    };
    await route.fulfill({ response, json: body });
  });

  await page.goto("/engine?tab=onchain");
  const view = page.getByTestId("engine-onchain-tab");
  await expect(view).toBeVisible({ timeout: 30_000 });
  await expect(view).toContainText("XYZ:SNDK");
  await expect(view).toContainText("롱→숏 전환");
  await expect(view).toContainText("검증중 N=0");

  const filter = view.locator(".whaleInstrumentFilter");
  await filter.getByLabel("고래 종목 검색").fill("SNDK");
  await filter.getByRole("button", { name: "보기" }).click();
  await expect(filter).toContainText("XYZ:SNDK");
  await expect(view.locator(".whaleFlowChartSection")).toContainText("XYZ:SNDK");
  await expect(view.locator(".whaleSymbolExposure")).toContainText("XYZ:SNDK 현재 쏠림");
  await expect(view.locator(".whaleSymbolExposure")).not.toContainText("BTC");
  await expect(view.locator(".whaleEventTape")).toContainText("4체결 합산");
  await expect(view.locator(".whaleEventTape")).not.toContainText("롱→숏 전환");

  await filter.getByLabel("고래 종목 검색").fill("NOT-TRACKED");
  await filter.getByRole("button", { name: "보기" }).click();
  await expect(filter.getByRole("status")).toContainText("찾지 못했습니다");
  await expect(view.locator(".whaleFlowChartSection")).toContainText("XYZ:SNDK");

  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(2);
});
