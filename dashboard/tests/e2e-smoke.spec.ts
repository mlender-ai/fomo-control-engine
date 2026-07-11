import { expect, test } from "@playwright/test";

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
  await expect(reviewGrid.getByText("판단 성적표", { exact: true })).toBeVisible();
  await expect(reviewGrid.getByText("계좌 성적표", { exact: true })).toBeVisible();
  await expect.poll(() => page.evaluate(() => (window as typeof window & { __fceSpaSentinel?: string }).__fceSpaSentinel)).toBe("alive");
});

test("live position cockpit smoke path", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("demo-mode-badge")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("position-strip")).toBeVisible();
  await expect(page.getByTestId("position-card")).toHaveCount(3);
  await expect(page.getByTestId("compact-chart-workspace")).toBeVisible();
  await expect(page.getByTestId("stance-ribbon")).toBeVisible();
  await expect(page.getByTestId("stance-hud")).toBeVisible();
  await expect(page.getByTestId("direction-gauge")).toHaveCount(0);
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
  await expect(page.getByTestId("direction-gauge")).toHaveCount(0);
  await expect(page.getByTestId("take-profit-gauge")).toHaveClass(/inactive/);

  await page.goto("/calibration");
  await expect(page.getByTestId("calibration-page")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("calibration-module-scorecard")).toBeVisible();
  await expect(page.getByTestId("calibration-module-confidence")).toBeVisible();
  await expect(page.getByTestId("calibration-module-levels")).toBeVisible();
  await expect(page.getByTestId("calibration-module-weekly")).toBeVisible();
});
