import { expect, test } from "@playwright/test";

const artifactDir = "test-results/chart-visual";

test("live position chart renders visual grammar states", async ({ page }) => {
  await page.goto("/");
  await page.waitForLoadState("domcontentloaded");

  const chartFrame = page.locator(".positionChartCanvasFrame");
  await chartFrame.waitFor({ state: "visible", timeout: 20_000 });

  await expect(page.locator(".positionSparkline").first()).toBeVisible({ timeout: 10_000 });
  await expect(page.locator(".healthGaugeRing").first()).toBeVisible({ timeout: 10_000 });
  await expect(page.locator(".triggerMeter")).toBeVisible({ timeout: 10_000 });
  await expect(page.locator(".chartGuideCallout")).toHaveCount(2, { timeout: 10_000 });
  await expect(page.locator(".volumeProfileOverlay text").filter({ hasText: "R:R" })).toHaveCount(1, { timeout: 10_000 });
  await expect(page.locator(".volumeProfileOverlay text").filter({ hasText: "무효화" })).toHaveCount(1, { timeout: 10_000 });
  await page.screenshot({ path: `${artifactDir}/plan.png`, fullPage: false });

  await toggleLayer(page, "레벨");
  await expect(page.locator(".volumeProfileOverlay text").filter({ hasText: "터치" }).first()).toBeVisible({ timeout: 10_000 });
  await page.screenshot({ path: `${artifactDir}/levels.png`, fullPage: false });

  await toggleLayer(page, "와이코프");
  await expect(page.locator(".volumeProfileOverlay text").filter({ hasText: "Phase" }).first()).toBeVisible({ timeout: 10_000 });
  await page.screenshot({ path: `${artifactDir}/wyckoff.png`, fullPage: false });

  await toggleLayer(page, "하모닉");
  const prz = page.locator(".volumeProfileOverlay text").filter({ hasText: "PRZ" }).first();
  if (await prz.count()) {
    await expect(prz).toBeVisible({ timeout: 10_000 });
  }
  await page.screenshot({ path: `${artifactDir}/harmonic.png`, fullPage: false });
});

async function toggleLayer(page: import("@playwright/test").Page, name: string) {
  const button = page.getByRole("button", { name, exact: true });
  await expect(button).toHaveCount(1);
  await button.click();
}
