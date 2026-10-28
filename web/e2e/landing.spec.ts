import { readFileSync } from "node:fs";
import path from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { parseShowcase } from "@/lib/showcase";
import { voicedCues } from "@/lib/showcase-derive";
import { SHOWCASE_ID } from "@/lib/site";

const showcase = parseShowcase(
  JSON.parse(readFileSync(path.join(__dirname, "..", "public", "showcase", SHOWCASE_ID, "showcase.json"), "utf8")),
  SHOWCASE_ID,
);
const voiced = voicedCues(showcase);
const firstLine = voiced[0]?.text ?? "";
/** The two soundtracks the hero plays: the video's own sound and the described mix. */
const SOUND = /\/showcase\/[\w-]+\/(video\.mp4|described-audio\.m4a)(\?.*)?$/;

/** "0:12.3 / 1:30.0" -> 12.3 */
async function playhead(page: Page): Promise<number> {
  const text = (await page.locator("[data-readout]").textContent()) ?? "";
  const m = /^(\d+):(\d+(?:\.\d+)?)/.exec(text.trim());
  return m ? Number(m[1]) * 60 + Number(m[2]) : NaN;
}

/** Keeps the hero on its silent 3x preview: the soundtracks never arrive. */
async function previewOnly(page: Page) {
  await page.route(SOUND, (route) => route.abort());
}

/** What one of the hero's soundtracks is doing. */
const track = (page: Page, which: "original" | "described") =>
  page.locator(`#horizon audio[data-track="${which}"]`).evaluate((a: HTMLAudioElement) => ({ paused: a.paused, t: a.currentTime }));

test.describe("landing", () => {
  test("has all ten sections as landmarks", async ({ page }) => {
    await page.goto("/");
    const sections = [
      "Audio description that waits for the silence.",
      "The missing half of a lecture",
      "How Lull works",
      "Zero overlap, by construction.",
      "A line it can’t prove, it doesn’t say.",
      "Six files, in formats players already read.",
      "Every line, with its evidence.",
      "From a terminal, an API or an agent.",
      "Evaluation in progress.",
    ];
    for (const name of sections) await expect(page.getByRole("region", { name, exact: true })).toHaveCount(1);
    await expect(page.getByRole("contentinfo")).toHaveCount(1);
    // nav anchors land on these
    for (const id of ["top", "problem", "how", "guarantee", "refusal", "library", "studio", "developers", "benchmarks"])
      await expect(page.locator(`section#${id}`)).toHaveCount(1);
  });

  test("the gate's second door takes you to describe your own clip", async ({ page }) => {
    await page.goto("/");
    const gate = page.getByRole("group", { name: "Choose how to begin" });
    const door = gate.getByRole("link", { name: "Describe your own clip" });
    await expect(door).toHaveAttribute("href", "/try");
    await door.click();
    // a client-side navigation: slower when the suite is busy
    await expect(page).toHaveURL(/\/try$/, { timeout: 15_000 });
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Watch a description being made.");
  });

  test("the horizon plays: the playhead moves and the first line is announced", async ({ page }) => {
    await previewOnly(page);
    await page.goto("/", { waitUntil: "networkidle" });
    const live = page.locator("[data-horizon-live]");
    await expect(live).toHaveAttribute("aria-live", "polite");
    await expect(live).toHaveText("");
    expect(await playhead(page)).toBe(0);

    await page.getByRole("button", { name: "Preview the timing" }).click();
    await expect(page.getByRole("button", { name: "Pause preview" })).toBeVisible();
    await expect.poll(() => playhead(page), { timeout: 3000 }).toBeGreaterThan(1);
    // the first line starts 9 s into the clip: 3 s of a 3x preview
    expect(voiced[0]?.start).toBe(9);
    await expect(live).toHaveText(firstLine, { timeout: 8000 });
    await expect(page.locator("[data-cue]").first()).toHaveAttribute("data-state", /typing|done/);

    // the gate and the transport drive one player
    await page.getByRole("button", { name: "Pause", exact: true }).click();
    await expect(page.getByRole("button", { name: "Resume preview" })).toBeVisible();
    const paused = await playhead(page);
    await page.waitForTimeout(400);
    expect(await playhead(page)).toBe(paused);

    await page.getByRole("button", { name: "Replay" }).click();
    await expect.poll(() => playhead(page)).toBeLessThan(paused);
    await expect(page.getByRole("button", { name: "Pause preview" })).toBeVisible();
  });

  test("with the run's own soundtracks the door offers to hear the difference, at 1x, and switches tracks in place", async ({ page }) => {
    await page.goto("/", { waitUntil: "networkidle" });
    const door = page.locator("[data-preview-door]");
    await expect(door).toHaveText("Hear the difference");
    await expect(page.getByText("3× preview")).toHaveCount(0);
    const soundtrack = page.getByRole("group", { name: "Soundtrack" });
    await expect(soundtrack.getByRole("button", { name: "Described" })).toHaveAttribute("aria-pressed", "true");

    await door.click();
    await expect(door).toHaveText("Pause");
    // the first data can take a moment to arrive when the machine is busy
    await expect.poll(async () => (await track(page, "described")).t, { timeout: 20_000 }).toBeGreaterThan(0.3);
    expect((await track(page, "described")).paused).toBe(false);
    expect((await track(page, "original")).paused).toBe(true);

    // real time, not the 3x preview: the clock follows the soundtrack
    const t0 = await playhead(page);
    const w0 = Date.now();
    await page.waitForTimeout(1500);
    const rate = ((await playhead(page)) - t0) / ((Date.now() - w0) / 1000);
    expect(rate).toBeGreaterThan(0.5);
    expect(rate).toBeLessThan(1.6);

    // the original takes over from the same moment
    const at = (await track(page, "described")).t;
    await soundtrack.getByRole("button", { name: "Original" }).click();
    await expect(soundtrack.getByRole("button", { name: "Original" })).toHaveAttribute("aria-pressed", "true");
    await expect.poll(async () => (await track(page, "original")).paused).toBe(false);
    expect((await track(page, "described")).paused).toBe(true);
    expect(Math.abs((await track(page, "original")).t - at)).toBeLessThan(0.8);

    await door.click();
    await expect(door).toHaveText("Resume");
    expect((await track(page, "original")).paused).toBe(true);
    expect((await track(page, "described")).paused).toBe(true);
  });

  test("sound that arrives while the silent preview plays takes over from the same moment", async ({ page }) => {
    let release: () => void = () => {};
    const arrived = new Promise<void>((resolve) => (release = resolve));
    await page.route(SOUND, async (route) => {
      await arrived;
      await route.continue();
    });
    // the held soundtracks would hold the load event
    await page.goto("/", { waitUntil: "domcontentloaded" });
    const door = page.locator("[data-preview-door]");
    await expect(door).toHaveText("Preview the timing");
    // interactive once the horizon has sized its canvas to the page (300 is a canvas's default width)
    await page.waitForFunction(() => (document.querySelector("#horizon canvas") as HTMLCanvasElement | null)?.width !== 300, null, { timeout: 15_000 });
    await door.click();
    await expect(door).toHaveText("Pause preview");
    await expect.poll(() => playhead(page)).toBeGreaterThan(3);
    release();
    // the sound starts where the preview was, and the door now pauses the sound
    await expect(door).toHaveText("Pause", { timeout: 15_000 });
    await expect.poll(async () => (await track(page, "described")).paused, { timeout: 5000 }).toBe(false);
    expect((await track(page, "described")).t).toBeGreaterThan(3);
    await expect(page.getByRole("group", { name: "Soundtrack" })).toBeVisible();
  });

  test("the silent preview stops when the horizon leaves the screen", async ({ page }) => {
    await previewOnly(page);
    await page.goto("/", { waitUntil: "networkidle" });
    await page.getByRole("button", { name: "Preview the timing" }).click();
    await expect.poll(() => playhead(page)).toBeGreaterThan(0.5);
    await page.locator("#developers").scrollIntoViewIfNeeded();
    await expect(page.getByRole("button", { name: "Resume preview" })).toBeAttached();
    const stopped = await playhead(page);
    await page.waitForTimeout(400);
    expect(await playhead(page)).toBe(stopped);
  });

  test("reduced motion shows the final state without playing", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await previewOnly(page);
    await page.goto("/", { waitUntil: "networkidle" });
    const lines = page.locator("[data-cue]");
    await expect(lines).toHaveCount(voiced.length);
    // every line is said in full; a line whose room a later one needed has stepped back
    for (let i = 0; i < voiced.length; i++) {
      await expect(lines.nth(i)).toHaveAttribute("data-state", /^(done|rested)$/);
      await expect(lines.nth(i)).toHaveText(voiced[i]?.text ?? "");
    }
    await expect(lines.last()).toHaveAttribute("data-state", "done");
    expect(await playhead(page)).toBe(0);
    await expect(page.getByRole("button", { name: "Preview the timing" })).toBeVisible();
    // the lamp does not breathe, so there is no lamp control
    await expect(page.getByRole("button", { name: "Pause lamp" })).toBeHidden();
  });

  test("the lines that show never overlap each other and stay on the stage", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await previewOnly(page);
    for (const viewport of [{ width: 1440, height: 900 }, { width: 900, height: 900 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(viewport);
      await page.goto("/", { waitUntil: "networkidle" });
      await page.evaluate(() => document.fonts.ready);
      const { stage, boxes, labels } = await page.evaluate(() => {
        const box = (el: Element) => {
          const r = el.getBoundingClientRect();
          return { left: r.left, right: r.right, top: r.top, bottom: r.bottom };
        };
        const boxes = [...document.querySelectorAll('[data-cue][data-state="done"]')].map(box);
        const labels = [...document.querySelectorAll("[data-gap]:not([data-tight])")].map((el) =>
          [...el.children].map((c) => {
            const r = c.getBoundingClientRect();
            return { left: r.left, right: r.right };
          }),
        );
        return { stage: box(document.querySelector("[data-horizon-stage]")!), boxes, labels };
      });
      expect(boxes.length, `at ${viewport.width}px`).toBeGreaterThan(1);
      for (const b of boxes) {
        expect(b.top, `at ${viewport.width}px`).toBeGreaterThanOrEqual(stage.top);
        expect(b.left).toBeGreaterThanOrEqual(stage.left - 1);
        expect(b.right).toBeLessThanOrEqual(stage.right + 1);
      }
      const sorted = [...boxes].sort((a, b) => a.left - b.left);
      for (let i = 1; i < sorted.length; i++) expect(sorted[i]!.left, `at ${viewport.width}px`).toBeGreaterThanOrEqual(sorted[i - 1]!.right);
      // pause labels that show don't run into each other
      const spans = labels.map((parts) => ({ left: Math.min(...parts.map((p) => p.left)), right: Math.max(...parts.map((p) => p.right)) }));
      for (let i = 1; i < spans.length; i++) expect(spans[i]!.left, `at ${viewport.width}px`).toBeGreaterThanOrEqual(spans[i - 1]!.right);
    }
  });

  test("developer tabs follow the ARIA tabs pattern", async ({ page }) => {
    await page.goto("/");
    const tab = (name: string) => page.getByRole("tab", { name });
    const panel = (name: string) => page.getByRole("tabpanel", { name });
    await expect(tab("CLI")).toHaveAttribute("aria-selected", "true");
    await expect(panel("CLI")).toBeVisible();
    await expect(panel("REST")).toBeHidden();
    await expect(tab("REST")).toHaveAttribute("tabindex", "-1");

    await tab("CLI").focus();
    await page.keyboard.press("ArrowRight");
    await expect(tab("REST")).toBeFocused();
    await expect(tab("REST")).toHaveAttribute("aria-selected", "true");
    await expect(panel("REST")).toBeVisible();
    await expect(panel("CLI")).toBeHidden();

    await page.keyboard.press("End");
    await expect(tab("MCP")).toBeFocused();
    await expect(panel("MCP")).toBeVisible();
    await page.keyboard.press("ArrowRight");
    await expect(tab("CLI")).toBeFocused();
    await page.keyboard.press("ArrowLeft");
    await expect(tab("MCP")).toBeFocused();
    await page.keyboard.press("Home");
    await expect(tab("CLI")).toBeFocused();
    await expect(tab("CLI")).toHaveAttribute("tabindex", "0");

    // Tab leaves the tab list for the code in the open panel
    await page.keyboard.press("Tab");
    await expect(panel("CLI").locator("pre")).toBeFocused();
  });

  test("the refusal strike draws once, on first view", async ({ page }) => {
    await page.goto("/", { waitUntil: "networkidle" });
    const sheet = page.locator("#refusal [data-pending]");
    await expect(sheet).toHaveCount(1);
    await page.locator("#refusal").scrollIntoViewIfNeeded();
    await expect(sheet).toHaveCount(0);
  });

  test("no serious or critical axe violations mid-preview", async ({ page }) => {
    await previewOnly(page);
    await page.goto("/", { waitUntil: "networkidle" });
    await page.getByRole("button", { name: "Preview the timing" }).click();
    await expect.poll(() => playhead(page)).toBeGreaterThan(10);
    await page.getByRole("button", { name: "Pause", exact: true }).click();
    const results = await new AxeBuilder({ page }).analyze();
    const blocking = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id);
    expect(blocking).toEqual([]);
  });
});
