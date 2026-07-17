import { expect, test, type Page } from "@playwright/test";

test.describe("chart visual regression", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      window.localStorage.clear();
    });
    await page.goto("/");
    await expect(page.getByTestId("demo-mode-badge")).toBeVisible({ timeout: 30_000 });
    await page.getByTestId("pro-mode-button").click();
    await expect(page.getByTestId("chart-layer-plan")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("chart-layer-ema")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("chart-canvas-frame")).toBeVisible({ timeout: 30_000 });
  });

  for (const state of [
    { name: "plan", layers: [] },
    { name: "levels", layers: ["levels"] },
    { name: "wyckoff", layers: ["wyckoff"] },
    { name: "liquidity", layers: ["liquidity"] },
    { name: "harmonic", layers: ["harmonic"] }
  ]) {
    test(`${state.name} layer snapshot`, async ({ page }) => {
      for (const layer of state.layers) {
        await toggleLayer(page, layer);
      }
      await waitForChartOverlay(page);
      if (state.name === "wyckoff") {
        await expect(page.getByTestId("wyckoff-layer-status")).toBeVisible();
        await expect(page.locator('[data-overlay-role="phase-label"]')).toHaveCount(0);
      }
      if (state.name === "harmonic") {
        await expect(page.getByTestId("harmonic-layer-status")).toContainText("하모닉 미검출");
        await expect(page.locator('[data-overlay-role="harmonic-candidate"]')).toHaveCount(0);
      }
      await expect(page.getByTestId("position-chart")).toHaveScreenshot(`chart-${state.name}.png`, {
        animations: "disabled",
        // Lightweight Charts and font rasterization vary by a few axis glyphs
        // across runners; keep the allowance below any structural UI change.
        maxDiffPixelRatio: 0.0026
      });
    });
  }
});

test("minimal neutral market ribbon snapshot", async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.clear();
  });
  await page.goto("/");
  await expect(page.getByTestId("demo-mode-badge")).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("minimal-asset-card").filter({ hasText: "ETHUSDT" }).click();
  await expect(page.getByTestId("compact-chart-workspace")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("stance-ribbon")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("stance-hud")).toBeVisible();
  await expect(page.locator("[data-event-pill]").first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("position-market-context")).toBeVisible();
  await expect(page.getByTestId("compact-chart-workspace")).toHaveScreenshot("chart-minimal-neutral.png", {
    animations: "disabled",
    // Linux runners can rasterize the dense Korean labels differently while
    // preserving every chart and layout boundary in this full-workspace shot.
    maxDiffPixelRatio: 0.012
  });
});

async function toggleLayer(page: Page, id: string) {
  const button = page.getByTestId(`chart-layer-${id}`);
  await expect(button).toBeVisible();
  await button.click();
}

async function waitForChartOverlay(page: Page) {
  await expect(page.getByTestId("chart-overlay")).toBeVisible();
  await page.waitForTimeout(500);
}
