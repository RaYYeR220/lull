import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { RUN_ID, own, routeApi } from "./try-api";

const ROUTES = [
  { path: "/", h1: "Audio description that waits for the silence." },
  { path: "/kitchen", h1: "Kitchen" },
] as const;

const PHONE = { width: 390, height: 844 };

/** The page's width against the viewport's, and any text or control outside it. */
function spillOf() {
  const root = document.documentElement;
  // text and controls must sit inside the viewport too, not merely be clipped by body;
  // the only exception is content inside a deliberate sideways scroller (the horizon, the studio timeline)
  const inScroller = (el: Element) => {
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      const o = getComputedStyle(p).overflowX;
      if (o === "auto" || o === "scroll") return true;
    }
    return false;
  };
  const spill = [...document.querySelectorAll("main a, main button, main h1, main h2, main h3, main p, main li, footer a, footer p, header a")]
    .filter((el) => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && (r.right > root.clientWidth + 1 || r.left < -1) && !inScroller(el);
    })
    .map((el) => `${el.tagName.toLowerCase()}: ${(el.textContent ?? "").trim().slice(0, 40)}`);
  return { scrollWidth: root.scrollWidth, clientWidth: root.clientWidth, spill };
}

function collectErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() !== "error") return;
    errors.push(`console: ${msg.text()}`);
  });
  page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
  return errors;
}

/**
 * Reflow (WCAG 1.4.10): at 320 CSS px, which is 1280 px at 400 % zoom, and at a phone's 390,
 * no page scrolls sideways and no text or control sits outside the viewport (content inside a
 * deliberate sideways scroller excepted).
 */
const REFLOW = ["/", "/try", "/watch", "/watch/ed90", "/watch/ed90/report", "/studio", "/studio/ed90", "/developers", "/benchmarks", `/run/${RUN_ID}`, `/run/${RUN_ID}/report`];

test("after moving to another page in the site, the next Tab is in that page's content", async ({ page }) => {
  await page.goto("/", { waitUntil: "networkidle" });
  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Developers" }).click();
  await expect(page).toHaveURL(/\/developers$/);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.locator("main#main")).toBeFocused();
  await page.keyboard.press("Tab");
  expect(await page.evaluate(() => !!document.activeElement?.closest("main"))).toBe(true);
});

for (const width of [320, 390]) {
  test(`no page scrolls sideways at ${width}px`, async ({ page }) => {
    test.setTimeout(120_000);
    await routeApi(page, own());
    await page.setViewportSize({ width, height: 800 });
    for (const path of REFLOW) {
      await page.goto(path, { waitUntil: "networkidle" });
      await page.evaluate(() => document.fonts.ready);
      const { scrollWidth, clientWidth, spill } = await page.evaluate(spillOf);
      expect(scrollWidth, `${path} at ${width}px`).toBeLessThanOrEqual(clientWidth);
      expect(spill, `${path} at ${width}px`).toEqual([]);
      // the nav, wrapped or not, sits clear of the page's content
      const under = await page.evaluate(() => {
        const nav = document.querySelector("header nav ul")!.getBoundingClientRect();
        return [...document.querySelectorAll("main h1, main h2, main p, main li, main a, main span")]
          .filter((el) => {
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && r.top < nav.bottom && r.bottom > nav.top && r.left < nav.right && r.right > nav.left;
          })
          .map((el) => (el.textContent ?? "").trim().slice(0, 30));
      });
      expect(under, `${path} at ${width}px: under the nav`).toEqual([]);
    }
  });
}

for (const route of ROUTES) {
  test.describe(`${route.path}`, () => {
    test("renders its landmarks and heading with no console errors", async ({ page }) => {
      const errors = collectErrors(page);
      const res = await page.goto(route.path, { waitUntil: "networkidle" });
      expect(res?.status()).toBe(200);
      await expect(page.getByRole("heading", { level: 1 })).toHaveText(route.h1);
      await expect(page.getByRole("banner")).toBeVisible();
      await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
      await expect(page.locator("main#main")).toHaveCount(1);
      await expect(page.getByRole("contentinfo")).toBeAttached();
      expect(errors).toEqual([]);
    });

    test("has no moderate, serious or critical axe violations", async ({ page }) => {
      for (const viewport of [{ width: 1440, height: 900 }, PHONE]) {
        await page.setViewportSize(viewport);
        // judge the resting state, not a frame of the entrance
        await page.emulateMedia({ reducedMotion: "reduce" });
        await page.goto(route.path, { waitUntil: "networkidle" });
        const results = await new AxeBuilder({ page }).analyze();
        const blocking = results.violations
          .filter((v) => v.impact === "moderate" || v.impact === "serious" || v.impact === "critical")
          .map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
        expect(blocking, `at ${viewport.width}px`).toEqual([]);
      }
    });

    test("has no horizontal scroll at 390px", async ({ page }) => {
      await page.setViewportSize(PHONE);
      await page.goto(route.path, { waitUntil: "networkidle" });
      await page.evaluate(() => document.fonts.ready);
      const { scrollWidth, clientWidth, spill } = await page.evaluate(spillOf);
      expect(scrollWidth).toBeLessThanOrEqual(clientWidth);
      expect(spill).toEqual([]);
    });

    test("skip link is the first stop and moves focus to main", async ({ page }) => {
      await page.goto(route.path, { waitUntil: "networkidle" });
      await page.keyboard.press("Tab");
      const skip = page.getByRole("link", { name: "Skip to content" });
      await expect(skip).toBeFocused();
      await expect(skip).toBeInViewport();
      await page.keyboard.press("Enter");
      await expect(page.locator("main#main")).toBeFocused();
    });

    test("the entrance never leaves content hidden", async ({ page }) => {
      await page.goto(route.path, { waitUntil: "networkidle" });
      // the longest entrance is .42s delay + .8s
      await page.waitForTimeout(1400);
      const hidden = await page.$$eval(".reveal", (els) =>
        els.filter((el) => Number(getComputedStyle(el).opacity) < 1).map((el) => el.className),
      );
      expect(hidden).toEqual([]);
    });
  });
}

test("reduced motion shows the resting state at once", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  const opacity = await page.locator("h1").evaluate((el) => getComputedStyle(el).opacity);
  expect(opacity).toBe("1");
});

test("the primary nav leads with Try it", async ({ page }) => {
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "Primary" });
  await expect(nav.getByRole("link")).toHaveText(["Try it", "Studio", "Library", "Developers", "Benchmarks"]);
  await expect(nav.getByRole("link", { name: "Try it" })).toHaveAttribute("href", "/try");
  await nav.getByRole("link", { name: "Try it" }).click();
  // a client-side navigation: slower when the suite is busy
  await expect(page).toHaveURL(/\/try$/, { timeout: 15_000 });
  await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Try it" })).toHaveAttribute("aria-current", "page");
});

test("kitchen is kept out of search", async ({ page }) => {
  await page.goto("/kitchen");
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", /noindex/);
});

test("unknown routes get the styled not-found page", async ({ page }) => {
  const res = await page.goto("/no-such-page");
  expect(res?.status()).toBe(404);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("There’s nothing at this address.");
  await expect(page.getByRole("link", { name: "Go to the home page" })).toBeVisible();
});

test("responses carry the security headers", async ({ request }) => {
  const res = await request.get("/");
  const h = res.headers();
  expect(h["x-content-type-options"]).toBe("nosniff");
  expect(h["referrer-policy"]).toBe("strict-origin-when-cross-origin");
  expect(h["x-frame-options"]).toBe("DENY");
  expect(h["permissions-policy"]).toBe("camera=(), microphone=(), geolocation=()");
});
