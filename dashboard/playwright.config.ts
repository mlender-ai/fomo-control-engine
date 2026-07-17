import { defineConfig, devices } from "@playwright/test";

const apiPort = process.env.PLAYWRIGHT_API_PORT ?? "8895";
const webPort = process.env.PLAYWRIGHT_WEB_PORT ?? "8896";
const apiBaseUrl = `http://127.0.0.1:${apiPort}`;
const webBaseUrl = process.env.PLAYWRIGHT_BASE_URL ?? `http://127.0.0.1:${webPort}`;

export default defineConfig({
  testDir: "./tests",
  snapshotPathTemplate: "{testDir}/__screenshots__/{testFilePath}/{platform}/{arg}{ext}",
  timeout: 30_000,
  fullyParallel: false,
  reporter: [["list"]],
  expect: {
    toHaveScreenshot: {
      // Keep structural regressions strict while tolerating subpixel font raster
      // variance observed across current Chromium macOS/Linux runners.
      maxDiffPixelRatio: 0.0012,
      threshold: 0.2
    }
  },
  use: {
    baseURL: webBaseUrl,
    ...devices["Desktop Chrome"],
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    viewport: { width: 1440, height: 900 }
  },
  webServer: [
    {
      command: `python3 -m uvicorn app.main:app --host 127.0.0.1 --port ${apiPort}`,
      cwd: "../backend",
      url: `${apiBaseUrl}/health`,
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
      env: {
        FCE_ENV: "e2e",
        FCE_DEMO_MODE: "true",
        FCE_CORS_ORIGINS: webBaseUrl,
        FCE_DATABASE_URL: process.env.FCE_E2E_DATABASE_URL ?? "sqlite:////tmp/fce-e2e.db",
        FCE_WORKER_ENABLED: "true",
        FCE_WORKER_STARTUP_DELAY_SECONDS: "1",
        FCE_WORKER_SYNC_POSITIONS_INTERVAL_SECONDS: "3",
        FCE_WORKER_REFRESH_MARKET_DATA_INTERVAL_SECONDS: "10",
        FCE_WORKER_REGEN_STALE_INSIGHTS_INTERVAL_SECONDS: "10",
        FCE_DERIVATIVE_TRACKING_INTERVAL_SECONDS: "10",
        FCE_WORKER_SCOUT_SCAN_INTERVAL_SECONDS: "10",
        FCE_TELEGRAM_ALERTS_ENABLED: "true",
        FCE_TELEGRAM_BOT_ENABLED: "false",
        FCE_OPENAI_API_KEY: "",
        FCE_COINGLASS_API_KEY: ""
      }
    },
    {
      command: `npm run build:e2e && npm run start -- -H 127.0.0.1 -p ${webPort}`,
      url: webBaseUrl,
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
      env: {
        FCE_NEXT_DIST_DIR: ".next-e2e",
        NEXT_PUBLIC_API_BASE_URL: apiBaseUrl
      }
    }
  ]
});
