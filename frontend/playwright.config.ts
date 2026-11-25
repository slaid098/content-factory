import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig, devices } from "@playwright/test";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const port = 8000;
const token = "playwright-delivery-token";

export default defineConfig({
  testDir: "tests/e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    extraHTTPHeaders: { Authorization: `Bearer ${token}` },
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "mobile-360",
      use: { ...devices["Desktop Chrome"], viewport: { width: 360, height: 800 } },
    },
    {
      name: "desktop-1280",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 900 } },
    },
  ],
  webServer: {
    command: `python -m uvicorn main:app --host 127.0.0.1 --port ${port}`,
    cwd: root,
    url: `http://127.0.0.1:${port}/api/v1/stock`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: {
      CONTENT_FACTORY_DATA_DIR:
        process.env.CONTENT_FACTORY_DATA_DIR ??
        join(tmpdir(), `content-factory-playwright-${process.pid}`),
      CONTENT_FACTORY_DELIVERY_TOKEN: token,
    },
  },
});
