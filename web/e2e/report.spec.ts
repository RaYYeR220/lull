import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { API, RUN_ID, RUNNING_ID, own, routeApi } from "./try-api";

/**
 * Conformance reports: a recorded run's, built from its export, and a live run's, loaded from
 * the API (the scripted stand-in). Only what the data says is shown.
 */
const PHONE = { width: 390, height: 844 };

test.describe("conformance reports", () => {
  test("a recorded run's report: WCAG with its blockers, coverage, timing and spend, from its export's summary", async ({ page }) => {
    await page.goto("/watch/ed90/report");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Elephants Dream");
    await expect(page.getByText("Conformance report, recorded run")).toBeVisible();
    await expect(page.locator("[data-no-summary]")).toHaveCount(0);
    const wcag = page.getByRole("region", { name: "WCAG audio description" });
    for (const c of ["1.2.3", "1.2.5", "1.2.7"]) await expect(wcag.locator(`[data-criterion="${c}"]`)).toContainText("Needs review");
    await expect(wcag.locator('[data-criterion="1.2.5"]')).toContainText("4 descriptions held for review");
    await expect(wcag.locator('[data-criterion="1.2.5"]')).toContainText("7 important events undescribed (8 of 15 described)");
    // the export's summary doesn't say whether a person reviewed the lines: nothing is claimed
    await expect(wcag.locator("[data-reviewed]")).toHaveCount(0);
    await expect(page.locator('[data-fact="coverage"]')).toContainText("8 of 15 (53 %) in the standard track; 10 of 15 (67 %) with the extended track");
    await expect(page.getByRole("region", { name: "Timing" }).locator('[data-fact="overlap"]')).toContainText("0.000 s");
    await expect(page.locator('[data-fact="f113"]').getByRole("listitem")).toHaveCount(3);
    const lines = page.getByRole("list", { name: "Lines by status" });
    await expect(lines).toContainText("8");
    await expect(lines).toContainText("held");
    await expect(page.getByText("Counted from the published lines.")).toHaveCount(0);
    await expect(page.locator('[data-fact="spend"]').first()).toContainText("$0.0480");
    await expect(page.getByRole("link", { name: "Watch it described" })).toHaveAttribute("href", "/watch/ed90");
  });

  test("the recorded run's page links to its report", async ({ page }) => {
    await routeApi(page, own());
    await page.goto("/watch/ed90");
    await page.getByRole("link", { name: "Conformance report" }).click();
    await expect(page).toHaveURL(/\/watch\/ed90\/report$/);
  });

  test("a live run's report: WCAG with its blockers, coverage, timing, held lines, models and spend", async ({ page }) => {
    await routeApi(page, own());
    await page.goto(`/run/${RUN_ID}/report`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("corridor-walk.mp4");
    const wcag = page.getByRole("region", { name: "WCAG audio description" });
    await expect(wcag.locator('[data-criterion="1.2.5"]')).toContainText("Needs review");
    await expect(wcag.locator('[data-criterion="1.2.5"]')).toContainText("1 description held for review");
    await expect(wcag.locator('[data-criterion="1.2.7"]')).toContainText("Not claimed");
    await expect(wcag.locator('[data-criterion="1.2.7"]')).toContainText("Extended version not produced");
    await expect(wcag.locator("[data-reviewed]")).toHaveText(/^Not every published line has been reviewed by a person yet/);
    await expect(page.locator('[data-fact="coverage"]')).toContainText("3 of 6 (50 %) in the standard track");
    await expect(page.locator('[data-fact="repetitive"]')).toContainText("Wire figures sway in the background.");
    await expect(page.locator('[data-fact="overlap"]')).toContainText("0.000 s");
    await expect(page.locator('[data-fact="f113"]')).toContainText("None");
    await expect(page.getByRole("region", { name: "The lines" })).toContainText("says a door opens; the frames show no door");
    await expect(page.getByText(/voiced with synthetic speech \(Magpie TTS Multilingual\); lines are awaiting review/)).toBeVisible();
    await expect(page.locator('[data-fact="models"]')).toContainText("nvidia/Nemotron-3-Ultra-550b-a55b");
    await expect(page.locator('[data-fact="spend"]').first()).toContainText("$0.0213");
    await expect(page.getByRole("link", { name: "The engine’s own report" })).toHaveAttribute("href", `${API}/v1/runs/${RUN_ID}/files/report.html`);
  });

  test("the live run's page and the studio link to its report", async ({ page }) => {
    await routeApi(page, own());
    await page.goto(`/run/${RUN_ID}`);
    await expect(page.getByRole("link", { name: "Conformance report" })).toHaveAttribute("href", `/run/${RUN_ID}/report`);
    await page.goto(`/studio/${RUN_ID}`);
    await expect(page.getByRole("link", { name: "Conformance report" })).toHaveAttribute("href", `/run/${RUN_ID}/report`);
    await page.goto("/studio/ed90");
    await expect(page.getByRole("link", { name: "Conformance report" })).toHaveAttribute("href", "/watch/ed90/report");
  });

  test("a run that isn't published has no report yet, and says why", async ({ page }) => {
    await routeApi(page, own());
    await page.goto(`/run/${RUNNING_ID}/report`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("This run is still being described.");
    await page.goto("/run/live-20261020-000000-gone00/report");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("There’s no run with that id.");
  });

  test("has no serious or critical axe violations and no sideways scroll, at 1440 and 390", async ({ page }) => {
    // four whole-page axe passes: slow when the suite runs in parallel
    test.setTimeout(180_000);
    await routeApi(page, own());
    for (const viewport of [{ width: 1440, height: 900 }, PHONE]) {
      await page.setViewportSize(viewport);
      for (const url of ["/watch/ed90/report", `/run/${RUN_ID}/report`]) {
        await page.goto(url);
        await expect(page.getByRole("heading", { level: 1 })).not.toHaveText("");
        await expect(page.getByRole("status").filter({ hasText: "Loading" })).toHaveCount(0);
        const results = await new AxeBuilder({ page }).analyze();
        const blocking = results.violations
          .filter((v) => v.impact === "serious" || v.impact === "critical")
          .map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
        expect(blocking, `${url} at ${viewport.width}px`).toEqual([]);
        const { scrollWidth, clientWidth } = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth }));
        expect(scrollWidth, `${url} at ${viewport.width}px`).toBeLessThanOrEqual(clientWidth);
      }
    }
  });
});
