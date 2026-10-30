import { defineConfig, devices } from "@playwright/test";

/** Uses the Real client with intercepted HTTP responses; no real model or RAG calls. */
export default defineConfig({
  testDir: "./e2e-mock",
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  outputDir: process.env.PLAYWRIGHT_OUTPUT_DIR ?? "test-results-mock",
  use: {
    baseURL: "http://127.0.0.1:4174",
    trace: "retain-on-failure",
    ...(process.env.PLAYWRIGHT_BROWSER_CHANNEL ? { channel: process.env.PLAYWRIGHT_BROWSER_CHANNEL } : {}),
    ...devices["Desktop Chrome"],
  },
  webServer: {
    command: "npm run dev -- --host 127.0.0.1 --port 4174 --strictPort",
    url: "http://127.0.0.1:4174",
    env: { VITE_RESEARCH_MODE: "real", VITE_API_BASE_URL: "http://127.0.0.1:8000" },
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
