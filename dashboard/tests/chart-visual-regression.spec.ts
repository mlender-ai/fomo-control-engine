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
      await expect(page.getByTestId("position-chart")).toHaveScreenshot(`chart-${state.name}.png`, {
        animations: "disabled"
      });
    });
  }
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
