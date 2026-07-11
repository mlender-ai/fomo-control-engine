import { expect, test } from "@playwright/test";

test("live position cockpit smoke path", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("demo-mode-badge")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("position-strip")).toBeVisible();
  await expect(page.getByTestId("position-card")).toHaveCount(3);
  await expect(page.getByTestId("compact-chart-workspace")).toBeVisible();
  await expect(page.getByTestId("direction-gauge")).toBeVisible();
  await expect(page.getByTestId("take-profit-gauge")).toBeVisible();
  await expect(page.getByTestId("position-chart")).toBeVisible();
  await expect(page.getByTestId("chart-canvas-frame")).toBeVisible();
  await expect(page.locator("[data-testid^='chart-layer-']")).toHaveCount(0);

  await page.getByTestId("pro-mode-button").click();
  await expect(page.getByTestId("verdict-bar")).toBeVisible();
  await expect(page.getByTestId("action-plan")).toBeVisible();
  await page.getByTestId("chart-layer-wyckoff").click();
  await expect(page.getByTestId("chart-overlay")).toBeVisible();
  await page.getByTestId("chart-layer-wyckoff").click();
  await page.getByTestId("chart-layer-liquidity").click();
  await expect(page.getByTestId("liquidity-layer")).toBeVisible();
  await expect(page.getByTestId("chart-layer-plan")).toHaveAttribute("aria-pressed", "true");
});

test("scout, analysis, simulator and calibration smoke paths", async ({ page }) => {
  await page.goto("/scout");
  await expect(page.getByTestId("demo-mode-badge")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scout-page")).toBeVisible();
  await page.getByPlaceholder("심볼 검색 (예: BTC, SOL) — 추가하면 관심종목에 담깁니다").fill("BTC");
  await expect(page.getByTestId("scout-quick-answer")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("compact-chart-workspace")).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("scout-quick-answer").getByRole("button", { name: "자세히", exact: true }).click();
  await expect(page.getByTestId("scout-analysis-view")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("compact-chart-workspace")).toBeVisible();
  await expect(page.getByTestId("direction-gauge")).toBeVisible();
  await expect(page.getByTestId("take-profit-gauge")).toHaveClass(/inactive/);

  await page.goto("/calibration");
  await expect(page.getByTestId("calibration-page")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("calibration-module-scorecard")).toBeVisible();
  await expect(page.getByTestId("calibration-module-confidence")).toBeVisible();
  await expect(page.getByTestId("calibration-module-levels")).toBeVisible();
  await expect(page.getByTestId("calibration-module-weekly")).toBeVisible();
});
