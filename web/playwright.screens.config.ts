import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

/** `pnpm test:screens`: only the screenshot capture, one worker so timings stay steady. */
export default defineConfig({
  ...base,
  testIgnore: [],
  testMatch: ["**/screens.spec.ts"],
  fullyParallel: false,
  workers: 1,
});
