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
  await expect(reviewGrid.getByText("엔진 상태", { exact: true })).toBeVisible();
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
  await expect(page.getByTestId("stance-hud")).toContainText("4h");
  await expect(page.getByTestId("stance-hud")).toContainText("상방 근거");
  await expect(page.getByTestId("position-market-context")).toBeVisible();
  await expect(page.getByTestId("direction-gauge")).toHaveCount(0);
  await expect(page.getByTestId("take-profit-gauge")).toBeVisible();
  await expect(page.getByTestId("position-chart")).toBeVisible();
  await expect(page.getByTestId("chart-canvas-frame")).toBeVisible();
  await expect(page.locator("[data-testid^='chart-layer-']")).toHaveCount(0);

  await page.getByTestId("position-card").filter({ hasText: "ETHUSDT" }).click();
  await expect(page.locator("[data-event-pill]").first()).toBeVisible({ timeout: 30_000 });

  await page.getByTestId("pro-mode-button").click();
  await expect(page.getByTestId("verdict-bar")).toBeVisible();
  await expect(page.getByTestId("action-plan")).toBeVisible();
  await expect(page.getByTestId("chart-layer-flow")).toHaveCount(0);
  await expect(page.getByTestId("chart-layer-indicators")).toHaveCount(0);
  await page.getByTestId("chart-layer-wyckoff").click();
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

  await page.goto("/calibration");
  await expect(page).toHaveURL(/\/engine\?tab=status/);
});
