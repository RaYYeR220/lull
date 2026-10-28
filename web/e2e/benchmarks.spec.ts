import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/**
 * The evaluation page. Whether there are results is decided when the site is built: the e2e
 * build is built from e2e/fixtures/benchmarks.sample.json (test data, marked so, made from the
 * scorer's own partial results file; scripts/build-e2e.mjs names it), so these tests see the
 * fixture, and nothing is fetched in the browser. The empty state (a build with no results)
 * is rendered in test/benchmarks-view.test.tsx; a file that doesn't hold fails the build
 * (test/benchmarks.test.ts).
 */
const PHONE = { width: 390, height: 844 };

const measure = (page: Page, id: string) => page.locator(`[data-measure="${id}"]`);

test.describe("benchmarks", () => {
  test("the fixture renders method first, every share with its interval, and says it is test data", async ({ page }) => {
    const fetched: string[] = [];
    page.on("request", (r) => {
      if (/benchmarks.*\.json/.test(r.url())) fetched.push(r.url());
    });
    await page.goto("/benchmarks");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("What Lull gets right, and what it misses.");
    await expect(page.getByText(/twelve short synthetic lectures/)).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Benchmarks" })).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("heading", { name: "The evaluation hasn’t been published yet." })).toHaveCount(0);
    await expect(page.locator("[data-fixture]")).toHaveText(/^Test fixture\. These numbers are test data/);
    await expect(page.locator("[data-partial]")).toContainText("Partial run (a smoke on a subset): not Suite A results.");
    const how = page.getByRole("region", { name: "What was measured, and how" });
    await expect(how).toContainText("Tag suite-a-prereg, commit 194d4d0803aa");
    await expect(how).toContainText("354967ad20310ab6013c5fd884520f57120dbaa56d81b927f2d0c3ec35b7ce96");
    await expect(how).toContainText("Commit 439ebeab3985");
    await expect(how.locator('[data-fact="prereg-calls"]')).toContainText("Every model call the results use came after the pre-registration commit.");

    // the drawing's cell is hidden from assistive tech: the three numbers are the row
    await expect(measure(page, "recall_standard").getByRole("cell")).toHaveText(["7.3 %", "5.9 % to 8.3 %", "3 of 41"]);
    await expect(measure(page, "recall_with_extended").getByRole("cell").first()).toHaveText("22.0 %");
    await expect(measure(page, "hallucination_claims").getByRole("cell").nth(2)).toHaveText("0 of 27");
    // the drawing shows the same numbers on one axis from 0 to 100 %
    const dot = measure(page, "recall_standard").locator("[class*='dot']");
    expect(await dot.evaluate((el) => (el as HTMLElement).style.left)).toBe("7.32%");
    await expect(page.locator("[aria-hidden='true'][class*='axis'] span").first()).toHaveText("0");

    await expect(page.locator('[data-stat="overlap"]')).toContainText("0.000 s");
    await expect(page.locator('[data-stat="decoys"]')).toContainText("0 of 8");
    // model calls and web searches together, with their interval; model calls alone without one
    const cost = page.locator('[data-stat="cost"]');
    await expect(cost).toContainText("$0.024");
    await expect(cost).toContainText("Model calls and web searches together; 95 % interval $0.023 to $0.025.");
    await expect(cost).toContainText("Model calls alone: $0.024 per minute; web searches: $0.000.");
    await expect(page.locator('[data-stat="speed"]')).toContainText("87.7 s");
    await expect(page.locator('[data-stat="speed"]')).toContainText("AMD Ryzen 5 5600X");

    await expect(page.locator("[data-supplementary]")).toHaveText(/^Added after the pre-registration/);
    await expect(measure(page, "voiced").getByRole("cell").first()).toHaveText("71.4 %");

    const videos = page.getByRole("region", { name: "Every video" });
    await expect(videos.locator("[data-video]")).toHaveCount(2);
    await expect(videos.locator('[data-video="a01"]').getByRole("cell")).toHaveText(["48.6 s", "25.6 s", "9 (7)", "2 of 24", "7 of 24", "0 of 12", "0.000 s", "$0.0184", "67.0 s"]);

    const judges = page.getByRole("region", { name: "How far to trust the judges" });
    await expect(judges).toContainText("Qwen/Qwen3-235B-A22B-Instruct-2507");
    await expect(judges.getByRole("row", { name: /Visual facts/ }).getByRole("cell")).toHaveText(["41", "92.7 %", "0.81"]);
    await expect(judges.getByRole("row", { name: /Claims/ }).getByRole("cell").last()).toHaveText("Undefined: neither judge varied");

    await expect(page.getByRole("region", { name: "Known failures" }).getByRole("heading", { level: 3 })).toHaveCount(2);
    // decided when the site was built: the browser asks for no results file
    expect(fetched).toEqual([]);
  });

  test("the landing links to the method", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("#benchmarks").getByRole("link", { name: "How it is measured" })).toHaveAttribute("href", "/benchmarks");
  });

  test("has no moderate, serious or critical axe violations and no sideways scroll, at 1440 and 390", async ({ page }) => {
    // whole-page axe passes: slow when the suite runs in parallel
    test.setTimeout(180_000);
    for (const viewport of [{ width: 1440, height: 900 }, PHONE]) {
      await page.setViewportSize(viewport);
      await page.goto("/benchmarks");
      await expect(page.locator("[data-fixture]")).toBeVisible();
      const results = await new AxeBuilder({ page }).analyze();
      const blocking = results.violations
        .filter((v) => v.impact === "moderate" || v.impact === "serious" || v.impact === "critical")
        .map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
      expect(blocking, `at ${viewport.width}px`).toEqual([]);
      const { scrollWidth, clientWidth } = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth }));
      expect(scrollWidth, `at ${viewport.width}px`).toBeLessThanOrEqual(clientWidth);
    }
  });
});
