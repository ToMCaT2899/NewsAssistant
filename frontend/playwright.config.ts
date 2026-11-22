import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  retries: 0,
  timeout: 30000,
  use: {
    browserName: "chromium",
    channel: "chrome",
    headless: true,
    baseURL: process.env.E2E_BASE_URL || "http://127.0.0.1:5175",
    viewport: { width: 1366, height: 900 },
    trace: "retain-on-failure",
  },
});
