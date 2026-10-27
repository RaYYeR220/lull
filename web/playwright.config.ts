import { existsSync } from "node:fs";
import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.E2E_PORT ?? 3217);
const BASE_URL = process.env.E2E_BASE_URL ?? `http://localhost:${PORT}`;

// E2E_CHROMIUM_PATH picks the browser. Without it, use the Chromium build on this
// machine when it is there, otherwise Playwright's own download.
const LOCAL_CHROMIUM = "C:/Users/egori/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe";
const executablePath = process.env.E2E_CHROMIUM_PATH || (existsSync(LOCAL_CHROMIUM) ? LOCAL_CHROMIUM : undefined);

export default defineConfig({
  testDir: "./e2e",
  // the screenshot capture runs on its own: `pnpm test:screens`
  testIgnore: ["**/screens.spec.ts"],
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 }, launchOptions: { executablePath } },
    },
  ],
  // Tests run against the production server: `pnpm build` first.
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
        command: `pnpm start --port ${PORT}`,
        url: BASE_URL,
        reuseExistingServer: !process.env.CI,
        timeout: 60_000,
      },
});
