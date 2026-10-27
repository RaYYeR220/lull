import { mkdirSync } from "node:fs";
import path from "node:path";
import { test } from "@playwright/test";

/**
 * Captures the hero at the two reference sizes into e2e/__screens__/ for a
 * side-by-side look against the approved design. Run with `pnpm test:screens` (not part of `pnpm test:e2e`).
 */
const OUT = path.join(__dirname, "__screens__");
const SIZES = [
  { name: "1440", viewport: { width: 1440, height: 900 } },
  { name: "390", viewport: { width: 390, height: 844 } },
];
const ROUTES = [
  { name: "home", path: "/" },
  { name: "kitchen", path: "/kitchen" },
];

test.describe("screens @screens", () => {
  test.beforeAll(() => mkdirSync(OUT, { recursive: true }));

  for (const size of SIZES) {
    for (const route of ROUTES) {
      test(`${route.name} at ${size.name}`, async ({ page }) => {
        await page.setViewportSize(size.viewport);
        await page.goto(route.path, { waitUntil: "networkidle" });
        await page.evaluate(() => document.fonts.ready);
        await page.waitForTimeout(2200);
        await page.screenshot({ path: path.join(OUT, `${route.name}-${size.name}.png`) });
        // pin the fixed grain to the document so a full-page capture is grained throughout
        await page.addStyleTag({ content: "body{position:relative} [data-grain]{position:absolute!important}" });
        await page.screenshot({ path: path.join(OUT, `${route.name}-${size.name}-full.png`), fullPage: true });
      });
    }
  }
});
