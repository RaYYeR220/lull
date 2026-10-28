import { readFileSync } from "node:fs";
import path from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { parseIndex } from "@/lib/showcase-schema.mjs";
import { parseShowcase } from "@/lib/showcase";
import { numberedCues } from "@/lib/showcase-derive";
import { formatTimecode } from "@/lib/timecode";
import { waitReady } from "./player-fixtures";
import { API, MOCK, apiDown, routeApi } from "./try-api";

/**
 * The recorded runs: /watch lists them from public/showcase/index.json, /watch/<id> plays one
 * with its script, checks and files, and forks it for the studio through the API (the
 * scripted stand-in, e2e/mock-api.mjs).
 */
const PUBLIC = path.join(__dirname, "..", "public");
const index = parseIndex(JSON.parse(readFileSync(path.join(PUBLIC, "showcase", "index.json"), "utf8")));
const show = parseShowcase(JSON.parse(readFileSync(path.join(PUBLIC, "showcase", "ed90", "showcase.json"), "utf8")), "ed90");
const cues = numberedCues(show);
const FORK_ID = "live-20261028-091500-f0c4e2";
const PHONE = { width: 390, height: 844 };

const player = (page: Page) => page.locator("lull-player");
const clock = (page: Page) => player(page).evaluate((el) => (el as unknown as { currentTime: number }).currentTime);
const row = (page: Page, id: string) => page.locator(`[data-line="${id}"]`);

function pageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(err.message));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(msg.text());
  });
  return errors;
}

test.describe("the library", () => {
  test("lists every recorded run in the index and opens its page", async ({ page }) => {
    await page.goto("/watch");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Recorded runs, ready to watch.");
    const cards = page.getByRole("main").getByRole("listitem").filter({ has: page.getByRole("heading", { level: 2 }) });
    await expect(cards).toHaveCount(index.length);
    for (const e of index) await expect(page.getByRole("link", { name: e.title })).toHaveAttribute("href", `/watch/${e.id}`);
    // the nav's Library is this page
    await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Library" })).toHaveAttribute("aria-current", "page");
    await routeApi(page, "ok");
    await page.getByRole("link", { name: "Elephants Dream" }).click();
    await expect(page).toHaveURL(/\/watch\/ed90$/, { timeout: 15_000 });
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Elephants Dream");
  });

  test("an id that isn't a recorded run is not found", async ({ page }) => {
    const res = await page.goto("/watch/nope");
    expect(res?.status()).toBe(404);
  });
});

test.describe("a recorded run's page", () => {
  test.beforeEach(async ({ page }) => {
    await routeApi(page, "ok");
  });

  test("plays the run's own files, says it is recorded, and credits the clip", async ({ page }) => {
    const errors = pageErrors(page);
    await page.goto("/watch/ed90");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Elephants Dream");
    await expect(page.getByText("Recorded run", { exact: true })).toBeVisible();
    await expect(page.getByText(/Nothing is sent to a model while you watch\./)).toBeVisible();
    await expect(page.getByRole("link", { name: /CC BY 2\.5/ })).toHaveAttribute("href", "https://creativecommons.org/licenses/by/2.5/");

    const p = player(page);
    await expect(p).toHaveAttribute("src", "/showcase/ed90/video.mp4");
    await expect(p).toHaveAttribute("descriptions", "/showcase/ed90/descriptions.vtt");
    await expect(p).toHaveAttribute("extended", "/showcase/ed90/extended.vtt");
    await expect(p).toHaveAttribute("described-audio", "/showcase/ed90/described-audio.m4a");
    await expect(p).toHaveAttribute("cue-audio-base", "/showcase/ed90/audio/");
    await expect(p).toHaveAttribute("cue-audio-ext", "m4a");
    await expect(p).toHaveAttribute("label", "Elephants Dream, described");
    await waitReady(page);
    const state = await p.evaluate((el) => (el as unknown as { state: Record<string, unknown> }).state);
    expect(state).toMatchObject({ video: "ready", descriptions: "ready", extended: "ready", describedAudio: "ready" });
    expect(errors).toEqual([]);
  });

  test("lists the script in time order: time, name, words and status", async ({ page }) => {
    await page.goto("/watch/ed90");
    const lines = page.locator("[data-line]");
    await expect(lines).toHaveCount(cues.length);
    for (const [i, c] of cues.entries()) {
      const line = lines.nth(i);
      await expect(line).toHaveAttribute("data-line", c.id);
      await expect(line).toContainText(formatTimecode(c.start));
      await expect(line).toContainText(c.label);
      await expect(line).toContainText(c.text);
      await expect(line.locator("[data-status]")).toHaveAttribute("data-status", c.status);
    }
    await expect(row(page, "x001")).toContainText("video holds 6.78 s");
    await expect(row(page, "c001")).toContainText("18 of 20 words");
  });

  test("choosing a line moves the player there and marks the line", async ({ page }) => {
    await page.goto("/watch/ed90");
    await waitReady(page);
    await row(page, "c005").getByRole("button").click();
    await expect.poll(() => clock(page)).toBeCloseTo(42.689, 1);
    await expect(row(page, "c005").getByRole("button")).toHaveAttribute("aria-current", "true");
    // focus stays on the line, and the keyboard works the same
    await expect(row(page, "c005").getByRole("button")).toBeFocused();
    await page.keyboard.press("Shift+Tab");
    await page.keyboard.press("Enter");
    await expect.poll(() => clock(page)).toBeCloseTo(cues.find((c) => c.id === "c002")!.start, 1);
    await expect(row(page, "c002").getByRole("button")).toHaveAttribute("aria-current", "true");
    await expect(row(page, "c005").getByRole("button")).not.toHaveAttribute("aria-current", "true");
    // an extended line: its freeze point
    await row(page, "x002").getByRole("button").click();
    await expect.poll(() => clock(page)).toBeCloseTo(4.917, 1);
  });

  test("on a phone, choosing a line brings the picture back into view", async ({ page }) => {
    await page.setViewportSize(PHONE);
    await page.goto("/watch/ed90");
    await waitReady(page);
    await row(page, "c013").scrollIntoViewIfNeeded();
    await expect(player(page)).not.toBeInViewport();
    await row(page, "c013").getByRole("button").click();
    await expect.poll(() => clock(page)).toBeCloseTo(83.21, 1);
    await expect(player(page)).toBeInViewport();
  });

  test("every file in the list is served", async ({ page, request }) => {
    await page.goto("/watch/ed90");
    const files = page.getByRole("region", { name: "Files from this run" }).getByRole("link");
    await expect(files).toHaveText(["descriptions.vtt", "extended.vtt", "described-audio.m4a", "video.mp4"]);
    for (const href of await files.evaluateAll((as) => as.map((a) => a.getAttribute("href") ?? ""))) {
      const res = await request.get(href);
      expect(res.status(), href).toBe(200);
      expect((await res.body()).length, href).toBeGreaterThan(100);
    }
  });

  test("what was checked comes from the run", async ({ page }) => {
    await page.goto("/watch/ed90");
    const checks = page.getByRole("region", { name: "What was checked" });
    await expect(checks.getByRole("definition").filter({ hasText: /^0\.00 s$/ })).toHaveCount(1);
    await expect(checks).toContainText("8 of 8");
    await expect(checks).toContainText("8 of 13 pauses carry a line");
    // the export's summary counts the four lines it held for review, though it doesn't list them
    await expect(checks).toContainText("8 of 12");
    await expect(checks).toContainText("4 held for review and not voiced; this export doesn’t list them.");
    await expect(checks).toContainText("Needs review");
    await expect(page.locator("[data-held-elsewhere]")).toContainText("4 more lines were held for review and not voiced.");
    await expect(page.getByRole("list", { name: "This run" })).toContainText("4 held for review");
    await expect(checks).toContainText("Not yet");
    await expect(checks).toContainText("$0.05");
    await expect(checks).toContainText("For the full run");
  });

  test("Make an editable copy forks the run, keeps its edit token and opens the copy in the studio", async ({ page }) => {
    const posted: string[] = [];
    page.on("request", (r) => {
      if (r.method() === "POST" && r.url().includes("/fork")) posted.push(r.url());
    });
    await page.goto("/watch/ed90");
    await expect(page.getByRole("link", { name: "Open in Studio" })).toHaveAttribute("href", "/studio/ed90");
    const door = page.getByRole("button", { name: "Make an editable copy" });
    await expect(door).toBeEnabled();
    await door.click();
    await expect(page).toHaveURL(new RegExp(`/studio/${FORK_ID}$`), { timeout: 15_000 });
    expect(posted).toEqual([`${API}/v1/runs/ed90/fork`]);
    const token = await page.evaluate((id) => localStorage.getItem(`lull-edit-token:${id}`), FORK_ID);
    expect(token).toBe("tok-fork-e2e-9876543210");
  });

  test("a refused copy says why, in the API's words, and can be tried again", async ({ page }) => {
    await routeApi(page, "fork429");
    await page.goto("/watch/ed90");
    const door = page.getByRole("button", { name: "Make an editable copy" });
    await door.click();
    await expect(page.getByRole("status").filter({ hasText: "Too many changes in a minute. Try again in 40 s." })).toBeVisible();
    await expect(door).toBeEnabled();
    await expect(page).toHaveURL(/\/watch\/ed90$/);
  });

  test("with the live service down, the copy door is disabled and says why", async ({ page }) => {
    await apiDown(page);
    await page.goto("/watch/ed90");
    const door = page.getByRole("button", { name: "Make an editable copy" });
    await expect(door).toBeDisabled();
    await expect(door).toHaveAccessibleDescription(/The live service isn’t answering, so a copy can’t be made right now\./);
    // the recorded run itself still plays
    await waitReady(page);
    await routeApi(page, "ok");
    await page.getByRole("button", { name: "Check again" }).click();
    await expect(door).toBeEnabled();
  });

  test("the copy door waits for the first health check, saying so", async ({ page }) => {
    let answer: () => void = () => {};
    const held = new Promise<void>((r) => (answer = r));
    await page.route(`${API}/v1/health`, async (route) => {
      await held;
      await route.continue({ url: route.request().url().replace(API, `${MOCK}/ok`) });
    });
    await page.goto("/watch/ed90");
    const door = page.getByRole("button", { name: "Make an editable copy" });
    await expect(door).toBeDisabled();
    await expect(door).toHaveAccessibleDescription("Checking that the live service is answering…");
    answer();
    await expect(door).toBeEnabled();
    await expect(page.getByText("A copy is yours to edit for a day, then the live service removes it.")).toBeVisible();
  });

  test("a copy this browser can't keep the edit key for says so, and opens only to look at", async ({ page }) => {
    await page.addInitScript(() => {
      const set = Storage.prototype.setItem;
      Storage.prototype.setItem = function (key: string, value: string) {
        if (key.startsWith("lull-edit-token:")) throw new DOMException("blocked", "SecurityError");
        return set.call(this, key, value);
      };
    });
    await page.goto("/watch/ed90");
    await page.getByRole("button", { name: "Make an editable copy" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Copy made, but this browser can’t keep the edit key" })).toBeVisible();
    await expect(page).toHaveURL(/\/watch\/ed90$/);
    await expect(page.getByRole("link", { name: "Open the copy to look at it" })).toHaveAttribute("href", `/studio/${FORK_ID}`);
  });

  test("has no serious or critical axe violations, with a line playing", async ({ page }) => {
    for (const viewport of [{ width: 1440, height: 900 }, PHONE]) {
      await page.setViewportSize(viewport);
      await page.emulateMedia({ reducedMotion: "reduce" });
      await page.goto("/watch/ed90", { waitUntil: "networkidle" });
      await waitReady(page);
      await row(page, "c002").getByRole("button").click();
      await expect(row(page, "c002").getByRole("button")).toHaveAttribute("aria-current", "true");
      const results = await new AxeBuilder({ page }).analyze();
      const blocking = results.violations
        .filter((v) => v.impact === "serious" || v.impact === "critical")
        .map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
      expect(blocking, `at ${viewport.width}px`).toEqual([]);
    }
    for (const viewport of [{ width: 1440, height: 900 }, PHONE]) {
      await page.setViewportSize(viewport);
      await page.goto("/watch", { waitUntil: "networkidle" });
      const results = await new AxeBuilder({ page }).analyze();
      const blocking = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id);
      expect(blocking, `library at ${viewport.width}px`).toEqual([]);
    }
  });

  test("has no horizontal scroll at 390px", async ({ page }) => {
    await page.setViewportSize(PHONE);
    for (const route of ["/watch/ed90", "/watch"]) {
      await page.goto(route, { waitUntil: "networkidle" });
      const { scrollWidth, clientWidth } = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth }));
      expect(scrollWidth, route).toBeLessThanOrEqual(clientWidth);
    }
  });
});
