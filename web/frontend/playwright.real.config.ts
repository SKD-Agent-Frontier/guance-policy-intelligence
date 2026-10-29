import { defineConfig, devices } from "@playwright/test";

/** Opt-in real-service smoke tests; never run these with Demo fixtures or on CI. */
export default defineConfig({
  testDir: "./e2e-real",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 240_000,
  reporter: "list",
  outputDir: process.env.PLAYWRIGHT_OUTPUT_DIR ?? "test-results-real",
  use: {
    baseURL: "http://127.0.0.1:5173",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    ...(process.env.PLAYWRIGHT_BROWSER_CHANNEL
      ? { channel: process.env.PLAYWRIGHT_BROWSER_CHANNEL }
      : {}),
    ...devices["Desktop Chrome"],
  },
  webServer: {
    command: "npm run dev -- --host 127.0.0.1 --port 5173 --strictPort",
    url: "http://127.0.0.1:5173",
    env: {
      VITE_RESEARCH_MODE: "real",
      VITE_API_BASE_URL: process.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000",
    },
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
