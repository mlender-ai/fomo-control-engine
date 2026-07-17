import { expect, test } from "@playwright/test";

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
    "/journal",
    "/markets",
    "/performance",
    "/positions",
    "/research",
    "/settings",
    "/shadow",
    "/trades",
    "/validation",
    "/calibration"
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
  await expect(page.getByTestId("action-plan")).toBeVisible();
  await expect(page.locator(".analysisChartColumn").getByTestId("money-flow-card")).toBeVisible();
  await expect(page.locator(".evidenceRoomRail").getByTestId("money-flow-card")).toHaveCount(0);
  const proPanelBox = await page.locator(".analysisChartColumn .positionChartPanel").boundingBox();
  const proFrameBox = await page.locator(".analysisChartColumn").getByTestId("chart-canvas-frame").boundingBox();
  expect(proPanelBox).not.toBeNull();
  expect(proFrameBox).not.toBeNull();
  expect(proPanelBox!.height - proFrameBox!.height).toBeLessThan(220);
  await expect(page.getByTestId("chart-layer-flow")).toHaveCount(0);
  await expect(page.getByTestId("chart-layer-indicators")).toHaveCount(0);
  await page.getByTestId("chart-layer-ema").click();
  await expect(page.getByTestId("chart-layer-ema")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("ema-ribbon-hud")).toBeVisible();
  await expect(page.getByTestId("ema-ribbon-hud")).toContainText("EMA 20–55");
  await page.getByTestId("chart-layer-wyckoff").click();
  await expect(page.getByTestId("ema-ribbon-hud")).toHaveCount(0);
  await expect(page.getByTestId("chart-overlay")).toBeVisible();
  await page.getByTestId("chart-layer-liquidity").click();
  await expect(page.getByTestId("chart-layer-wyckoff")).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByTestId("liquidity-layer")).toBeVisible();
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

test("scout and analysis smoke paths", async ({ page }) => {
  await page.goto("/scout");
  await expect(page.getByTestId("demo-mode-badge")).toBeVisible({ timeout: 30_000 });
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

test("engine trading workspace and absorbed calibration route", async ({ page }) => {
  await page.goto("/engine");
  await expect(page.getByTestId("engine-trading-page")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("engine-battle-tab")).toBeVisible();
  await page.getByRole("link", { name: "엔진 포지션" }).click();
  await expect(page).toHaveURL(/tab=positions/);
  await page.getByRole("link", { name: "거래 일지" }).click();
  await expect(page.getByTestId("engine-journal-tab")).toBeVisible();
  await page.getByRole("link", { name: "엔진 상태" }).click();
  await expect(page.getByTestId("engine-status-tab")).toBeVisible();
  await expect(page.getByTestId("paper-gate-funnel")).toBeVisible();
  await expect(page.getByTestId("event-pill-diagnostics")).toBeVisible();

  await page.goto("/calibration");
  await expect(page).toHaveURL(/\/engine\?tab=status/);
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
