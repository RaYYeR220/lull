import { mkdirSync } from "node:fs";
import path from "node:path";
import { test, type Page } from "@playwright/test";

/**
 * Captures the pages at the two reference sizes into e2e/__screens__/ for a side-by-side
 * look against the approved design. The home flow repeats the design's own capture: rest,
 * then a paused preview, then a scroll through (so the strike has drawn) and a full page.
 * Run with `pnpm test:screens` (not part of `pnpm test:e2e`).
 */
const OUT = path.join(__dirname, "__screens__");
const SIZES = [
  { name: "1440", viewport: { width: 1440, height: 900 }, preview: 9500 },
  { name: "390", viewport: { width: 390, height: 844 }, preview: 4000 },
];

async function fullPage(page: Page, file: string) {
  // pin the fixed grain to the document so a full-page capture is grained throughout
  await page.addStyleTag({ content: "body{position:relative} [data-grain]{position:absolute!important}" });
  await page.screenshot({ path: path.join(OUT, file), fullPage: true });
}

test.describe("screens @screens", () => {
  test.beforeAll(() => mkdirSync(OUT, { recursive: true }));
  test.describe.configure({ timeout: 90_000 });

  for (const size of SIZES) {
    test(`home at ${size.name}`, async ({ page }) => {
      await page.setViewportSize(size.viewport);
      await page.goto("/", { waitUntil: "networkidle" });
      await page.evaluate(() => document.fonts.ready);
      await page.waitForTimeout(2200);
      await page.screenshot({ path: path.join(OUT, `home-${size.name}.png`) });

      const door = page.locator("[data-preview-door]");
      await door.click();
      await page.waitForTimeout(size.preview);
      await door.click();
      await page.screenshot({ path: path.join(OUT, `home-${size.name}-preview.png`) });

      const height = await page.evaluate(() => document.documentElement.scrollHeight);
      for (let y = 0; y < height; y += size.viewport.height / 2) {
        await page.evaluate((top) => window.scrollTo(0, top), y);
        await page.waitForTimeout(120);
      }
      await page.waitForTimeout(1800);
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.waitForTimeout(300);
      await fullPage(page, `home-${size.name}-full.png`);
    });

    test(`home final state at ${size.name}`, async ({ page }) => {
      await page.emulateMedia({ reducedMotion: "reduce" });
      await page.setViewportSize(size.viewport);
      await page.goto("/", { waitUntil: "networkidle" });
      await page.evaluate(() => document.fonts.ready);
      await page.waitForTimeout(800);
      await page.screenshot({ path: path.join(OUT, `home-${size.name}-final.png`) });
    });

    test(`kitchen at ${size.name}`, async ({ page }) => {
      await page.setViewportSize(size.viewport);
      await page.goto("/kitchen", { waitUntil: "networkidle" });
      await page.evaluate(() => document.fonts.ready);
      await page.waitForTimeout(2200);
      await page.screenshot({ path: path.join(OUT, `kitchen-${size.name}.png`) });
      await fullPage(page, `kitchen-${size.name}-full.png`);
    });
  }
});
