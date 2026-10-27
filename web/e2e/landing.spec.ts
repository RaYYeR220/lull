import { readFileSync } from "node:fs";
import path from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { parseShowcase } from "@/lib/showcase";
import { closeUp, speechOverlap, spokenFromDraft, verifiedCue, voicedCues } from "@/lib/showcase-derive";
import { SHOWCASE_ID } from "@/lib/site";

const showcase = parseShowcase(
  JSON.parse(readFileSync(path.join(__dirname, "..", "public", "showcase", SHOWCASE_ID, "showcase.json"), "utf8")),
  SHOWCASE_ID,
);
const voiced = voicedCues(showcase);
const firstLine = voiced[0]?.text ?? "";

/** "0:12.3 / 1:30.0" -> 12.3 */
async function playhead(page: Page): Promise<number> {
  const text = (await page.locator("[data-readout]").textContent()) ?? "";
  const m = /^(\d+):(\d+(?:\.\d+)?)/.exec(text.trim());
  return m ? Number(m[1]) * 60 + Number(m[2]) : NaN;
}

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
    await page.goto("/", { waitUntil: "networkidle" });
    const live = page.locator("[data-horizon-live]");
    await expect(live).toHaveAttribute("aria-live", "polite");
    await expect(live).toHaveText("");
    expect(await playhead(page)).toBe(0);

    await page.getByRole("button", { name: "Preview the timing" }).click();
    await expect(page.getByRole("button", { name: "Pause preview" })).toBeVisible();
    await expect.poll(() => playhead(page), { timeout: 3000 }).toBeGreaterThan(1);
    // the first line starts 9 s into the clip: 3 s of a 3x preview
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

  test("the silent preview stops when the horizon leaves the screen", async ({ page }) => {
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
    await page.goto("/", { waitUntil: "networkidle" });
    const lines = page.locator("[data-cue]");
    await expect(lines).toHaveCount(voiced.length);
    for (let i = 0; i < voiced.length; i++) {
      await expect(lines.nth(i)).toHaveAttribute("data-state", "done");
      await expect(lines.nth(i)).toHaveText(voiced[i]?.text ?? "");
    }
    expect(await playhead(page)).toBe(0);
    await expect(page.getByRole("button", { name: "Preview the timing" })).toBeVisible();
    // the lamp does not breathe, so there is no lamp control
    await expect(page.getByRole("button", { name: "Pause lamp" })).toBeHidden();
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
    await page.goto("/", { waitUntil: "networkidle" });
    await page.getByRole("button", { name: "Preview the timing" }).click();
    await expect.poll(() => playhead(page)).toBeGreaterThan(10);
    await page.getByRole("button", { name: "Pause", exact: true }).click();
    const results = await new AxeBuilder({ page }).analyze();
    const blocking = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id);
    expect(blocking).toEqual([]);
  });
});

test.describe("showcase data", () => {
  test("every number on the page is derived from the run", () => {
    expect(speechOverlap(showcase)).toEqual({ cues: 0, seconds: 0 });
    // held cues are never voiced
    expect(voiced.every((c) => c.status !== "held" && c.status !== "extended")).toBe(true);
    const cu = closeUp(showcase);
    expect(cu?.gap.id).toBe("g003");
    expect(cu?.before.map((w) => w.text).join(" ")).toBe("Hurry about");
    expect(cu?.after.map((w) => w.text).join(" ")).toBe("it.");
    const v = showcase.verifyExample;
    expect(v && spokenFromDraft(v)).toBe("A bald man in a green coat stands by a window.");
    expect(verifiedCue(showcase)?.index).toBe(0);
  });

  test("a malformed file is refused with a reason", () => {
    expect(() => parseShowcase({ ...showcase, cues: [{ id: "c1", start: 1, end: 2, text: "x", status: "maybe", ahead: false }] }, SHOWCASE_ID)).toThrow(
      /cues\[0\]\.status/,
    );
    expect(() => parseShowcase({ ...showcase, id: "other" }, SHOWCASE_ID)).toThrow(/id/);
    expect(() => parseShowcase({ ...showcase, audio: { original: "https://elsewhere/a.m4a", described: "/x" } }, SHOWCASE_ID)).toThrow(/audio/);
  });
});
