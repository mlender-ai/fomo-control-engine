import { expect, test } from "@playwright/test";

test("live position cockpit smoke path", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("demo-mode-badge")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("position-strip")).toBeVisible();
  await expect(page.getByTestId("position-card")).toHaveCount(3);
  await expect(page.getByTestId("position-sparkline").first()).toBeVisible();
  await expect(page.getByTestId("health-gauge").first()).toBeVisible();
  await expect(page.getByTestId("verdict-bar")).toBeVisible();
  await expect(page.getByTestId("action-plan")).toBeVisible();
  await expect(page.getByTestId("position-chart")).toBeVisible();
  await expect(page.getByTestId("chart-canvas-frame")).toBeVisible();
  await expect(page.getByTestId("trigger-meter")).toBeVisible();

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
  await page.getByRole("button", { name: "스캔", exact: true }).click();
  await expect(page.getByTestId("scout-table")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("scout-row").first()).toBeVisible();
  await page.getByTestId("scout-row").first().click();
  await expect(page.getByTestId("scout-analysis-view")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("entry-simulator")).toBeVisible();
  await page.getByTestId("simulator-run").click();
  await expect(page.getByTestId("simulator-result")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("simulator-checklist")).toBeVisible();

  await page.goto("/calibration");
  await expect(page.getByTestId("calibration-page")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("calibration-module-scorecard")).toBeVisible();
  await expect(page.getByTestId("calibration-module-confidence")).toBeVisible();
  await expect(page.getByTestId("calibration-module-levels")).toBeVisible();
  await expect(page.getByTestId("calibration-module-weekly")).toBeVisible();
});
