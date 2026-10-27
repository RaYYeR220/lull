import { mkdirSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { routeFixtures, waitReady } from "./player-fixtures";
import { routeApi } from "./try-api";

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

/**
 * The player inside the site (the kitchen instance, with the fixture media routed in place
 * of the recorded run's): each mode mid-line, the menu, the panels, an extended hold and the
 * missing-media state, at both sizes. Element captures: e2e/__screens__/player-<state>-<size>.png.
 */
test.describe("player screens @screens", () => {
  test.beforeAll(() => mkdirSync(OUT, { recursive: true }));
  test.describe.configure({ timeout: 90_000 });

  const MODES = [
    { file: "mixed", radio: "Mixed audio" },
    { file: "spoken", radio: "Spoken cues" },
    { file: "screen-reader", radio: "My screen reader" },
  ];

  for (const size of SIZES) {
    test(`player at ${size.name}`, async ({ page }) => {
      await page.setViewportSize(size.viewport);
      await routeFixtures(page, "**/showcase/ed90/**", "/showcase/ed90/");
      await page.goto("/kitchen#player", { waitUntil: "networkidle" });
      await waitReady(page);
      await page.evaluate(() => document.fonts.ready);
      const p = page.locator("lull-player");
      const shot = (state: string) => p.screenshot({ path: path.join(OUT, `player-${state}-${size.name}.png`) });
      const modeButton = p.getByRole("button", { name: /^Description mode/ });
      const seek = (t: number) => p.evaluate((el, at) => (el as unknown as { seek(t: number): void }).seek(at), t);
      await p.scrollIntoViewIfNeeded();
      await shot("rest");

      for (const m of MODES) {
        await modeButton.click();
        await p.getByRole("radio", { name: m.radio }).check();
        if ((await modeButton.getAttribute("aria-expanded")) === "true") await page.keyboard.press("Escape");
        await seek(1.15);
        await p.getByRole("button", { name: "Play" }).click();
        await page.waitForTimeout(450);
        await shot(m.file);
        await p.getByRole("button", { name: "Pause" }).click();
      }

      // extended: caught during the hold
      await modeButton.click();
      await p.getByRole("radio", { name: "Extended" }).check();
      await seek(2.3);
      await p.getByRole("button", { name: "Play" }).click();
      await expect.poll(() => p.evaluate((el) => (el as unknown as { state: { holding: boolean } }).state.holding)).toBe(true);
      await page.waitForTimeout(300);
      await shot("extended");
      await p.getByRole("button", { name: "Pause" }).click();

      await modeButton.click();
      await page.waitForTimeout(100);
      await page.screenshot({ path: path.join(OUT, `player-menu-${size.name}.png`) });
      await page.keyboard.press("Escape");

      await p.getByRole("button", { name: "Transcript" }).click();
      await seek(3.7);
      if (size.name === "1440") await p.getByRole("button", { name: "Keys" }).click();
      await page.waitForTimeout(150);
      await shot("panels");
      await p.getByRole("button", { name: "Transcript" }).focus();
      await page.keyboard.press("Tab");
      await shot("focus");
    });

    test(`player, media missing, at ${size.name}`, async ({ page }) => {
      await page.setViewportSize(size.viewport);
      await page.goto("/kitchen#player", { waitUntil: "networkidle" });
      await page.evaluate(() => document.fonts.ready);
      const p = page.locator("lull-player");
      await expect(p.getByText("This video isn’t available")).toBeVisible();
      await p.scrollIntoViewIfNeeded();
      await p.screenshot({ path: path.join(OUT, `player-missing-${size.name}.png`) });
    });
  }
});

/**
 * The try-it page against the scripted API: the picker, a run held mid-finalize (two cues in,
 * the sweep at the second), a finished run and a refusal, at both sizes. Viewport captures
 * plus full pages: e2e/__screens__/try-<state>-<size>(-full).png.
 */
test.describe("try screens @screens", () => {
  test.beforeAll(() => mkdirSync(OUT, { recursive: true }));
  test.describe.configure({ timeout: 90_000 });

  for (const size of SIZES) {
    test(`try at ${size.name}`, async ({ page }) => {
      await page.setViewportSize(size.viewport);
      await routeApi(page, "mid");
      await page.goto("/try", { waitUntil: "networkidle" });
      await page.evaluate(() => document.fonts.ready);
      await expect(page.getByRole("button", { name: "Describe Elephants Dream" })).toBeEnabled();
      await page.waitForTimeout(900);
      await page.screenshot({ path: path.join(OUT, `try-pick-${size.name}.png`) });
      await fullPage(page, `try-pick-${size.name}-full.png`);

      await page.getByRole("button", { name: "Describe Elephants Dream" }).click();
      await expect(page.locator("li[data-cue]")).toHaveCount(2);
      await page.waitForTimeout(1200);
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: path.join(OUT, `try-mid-${size.name}.png`) });
      await fullPage(page, `try-mid-${size.name}-full.png`);

      await routeApi(page, "run");
      await page.goto("/try", { waitUntil: "networkidle" });
      await page.getByRole("button", { name: "Describe Elephants Dream" }).click();
      await expect(page.getByRole("heading", { name: /^Ready to watch/ })).toBeVisible({ timeout: 15_000 });
      await page.waitForTimeout(900);
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: path.join(OUT, `try-done-${size.name}.png`) });
      await fullPage(page, `try-done-${size.name}-full.png`);

      await routeApi(page, "429");
      await page.goto("/try", { waitUntil: "networkidle" });
      await page.getByRole("button", { name: "Describe Elephants Dream" }).click();
      await expect(page.getByRole("heading", { name: "You’ve reached the live limit for now." })).toBeVisible();
      await page.waitForTimeout(300);
      await page.screenshot({ path: path.join(OUT, `try-refused-${size.name}.png`) });
    });
  }
});
