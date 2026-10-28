import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { waitReady } from "./player-fixtures";
import { API, FORK_ID, FORK_TOKEN, RUN_ID, RUNNING_ID, apiDown, holdToken, own, routeApi } from "./try-api";

/**
 * The studio, /studio/<id>, against the scripted API (e2e/mock-runs.mjs): a copy of the recorded
 * run this browser holds the edit key for, the live run of an uploaded clip, and the recorded
 * run itself (read-only). Each test has a scenario of its own, so edits never meet.
 */
const PHONE = { width: 390, height: 844 };

const drawer = (page: Page) => page.locator("[data-drawer]");
const option = (page: Page, id: string) => page.locator(`[role="option"][data-cue="${id}"]`);
const line = (page: Page) => drawer(page).getByRole("textbox", { name: "The line" });
const save = (page: Page) => drawer(page).getByRole("button", { name: /Save and check again|Checking…/ });
const outcome = (page: Page) => drawer(page).getByRole("status");
const publish = (page: Page) => page.getByRole("button", { name: /^Publish/ });

async function openCopy(page: Page, cue: string, behaviour = "studio") {
  await holdToken(page, FORK_ID, FORK_TOKEN);
  await routeApi(page, own(behaviour));
  await page.goto(`/studio/${FORK_ID}?cue=${cue}`);
  await expect(drawer(page)).toBeVisible({ timeout: 15_000 });
}

function pageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(err.message));
  page.on("console", (msg) => {
    if (msg.type() === "error" && !/Failed to load resource|ERR_CONNECTION_REFUSED/.test(msg.text())) errors.push(msg.text());
  });
  return errors;
}

async function axe(page: Page, where: string) {
  const results = await new AxeBuilder({ page }).analyze();
  const blocking = results.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
  expect(blocking, where).toEqual([]);
}

test.describe("the studio, editing a copy", () => {
  test("opens the copy with its timeline, the chosen line in the drawer and the run's summary", async ({ page }) => {
    const errors = pageErrors(page);
    await openCopy(page, "c002");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Elephants Dream");
    await expect(page.getByText("Studio, your run")).toBeVisible();
    await expect(page.getByText(/This copy is kept until/)).toHaveCount(0); // no record of when it was made in this test
    // the summary, from the run's own published summary
    await expect(page.getByText("6 of 6", { exact: true })).toBeVisible();
    await expect(page.getByRole("list", { name: "WCAG statuses" }).getByRole("listitem").filter({ hasText: "1.2.5" })).toContainText("Met (automated checks)");
    await page.getByText("Moments the planner rated as repeats, not counted as missing").click();
    await expect(page.getByText("Wire figures sway in the background.")).toBeVisible();

    // the timeline: soundtrack (lent by the recorded run of the clip), speech, pauses and lines
    await expect(page.getByRole("listbox", { name: "Lines, in time order" }).getByRole("option")).toHaveCount(10);
    await expect(option(page, "c002")).toHaveAttribute("aria-selected", "true");
    await expect(option(page, "c002")).toHaveAccessibleName(/^Cue 2, Verified, at 0:18\.35: The two people are surrounded by wires\.$/);
    await expect(page.getByText("The soundtrack levels are from the recorded run of this clip, Elephants Dream.")).toBeVisible();

    // the drawer: where it sits, its frame, words against the pause, claims, note, provenance
    await expect(drawer(page).getByRole("heading", { level: 2 })).toHaveText("Cue 2");
    await expect(drawer(page)).toContainText("In the pause from 0:18.35 to 0:23.41, 5.06 s.");
    await expect(drawer(page).getByRole("img")).toHaveCount(0); // decorative frame: alt=""
    await expect(drawer(page).locator("img")).toHaveAttribute("src", `${API}/v1/runs/${FORK_ID}/files/keyframes/c002.jpg`);
    await expect(line(page)).toHaveValue("The two people are surrounded by wires.");
    await expect(line(page)).toHaveAccessibleDescription(/7 of 15 words/);
    await expect(drawer(page).getByText("removed “move across a narrow metal walkway” (not visible in the frames)")).toBeVisible();
    await expect(drawer(page).getByRole("listitem").filter({ hasText: "two people" })).toContainText("Seen at 0:18.95");
    await expect(drawer(page).getByRole("list", { name: "Who made this line" })).toContainText("Checked by Nemotron 3.5 Super VL");
    await expect(drawer(page).getByRole("list", { name: "Who made this line" })).toContainText("Voiced by Magpie TTS Multilingual");

    // nothing changed yet: publish waits, and says why
    await expect(publish(page)).toHaveAttribute("aria-disabled", "true");
    await expect(publish(page)).toHaveAccessibleDescription("Nothing to publish: no change since the last publish.");
    expect(errors).toEqual([]);
  });

  test("the word budget follows the text as it is typed", async ({ page }) => {
    await openCopy(page, "c002");
    await line(page).fill("The two people stand among hanging wires and cables, looking up at a tall shaft full of moving lights.");
    await expect(line(page)).toHaveAccessibleDescription(/4 words over: 19 of 15/);
    await expect(line(page)).toHaveAttribute("aria-invalid", "true");
    await expect(drawer(page).getByText(/Longer than its pause holds/)).toBeVisible();
    await line(page).fill("Two people stand among wires.");
    await expect(line(page)).toHaveAccessibleDescription(/5 of 15 words/);
    await drawer(page).getByRole("button", { name: "Undo my changes" }).click();
    await expect(line(page)).toHaveValue("The two people are surrounded by wires.");
  });

  test("an edit is checked again and comes back edited, with its checks; the run then needs publishing", async ({ page }) => {
    const patched: { auth?: string; body?: string }[] = [];
    page.on("request", (r) => {
      if (r.method() === "PATCH") patched.push({ auth: r.headers()["authorization"], body: r.postData() ?? undefined });
    });
    await openCopy(page, "c002", "slow");
    await line(page).fill("The two people stand among hanging wires, the man looking up.");
    await expect(line(page)).toHaveAccessibleDescription(/11 of 15 words/);
    await save(page).click();
    await expect(outcome(page)).toContainText("Checking the new line: writing rules, then the frames, then the voice.");
    await expect(outcome(page)).toHaveText("Cue 2 is edited: it passed the frame check and was voiced again.", { timeout: 15_000 });
    expect(patched).toEqual([{ auth: `Bearer ${FORK_TOKEN}`, body: JSON.stringify({ text: "The two people stand among hanging wires, the man looking up." }) }]);

    await expect(drawer(page).locator("[data-status]").first()).toHaveAttribute("data-status", "edited");
    await expect(option(page, "c002")).toHaveAttribute("data-status", "edited");
    // the reviewer warning the API's lint found, and who wrote it now
    await expect(drawer(page).getByRole("region", { name: "Writing checks" })).toContainText("R14-gender");
    await expect(drawer(page).getByRole("region", { name: "Writing checks" })).toContainText("Advice");
    await expect(drawer(page).getByRole("list", { name: "Who made this line" })).toContainText("Written by a person");
    await expect(page.getByText("Changed since the last publish")).toBeVisible();
    await expect(page.getByText("The player plays the last published version. Publish to hear your changes in it.")).toBeVisible();
    await expect(publish(page)).not.toHaveAttribute("aria-disabled", "true");
    // the player fetches the new takes afresh
    await expect(page.locator("lull-player")).toHaveAttribute("cue-audio-base", `${API}/v1/runs/${FORK_ID}/cues/{id}/audio?v=1`);
  });

  test("an edit the frames don't support comes back held, with the reason", async ({ page }) => {
    await openCopy(page, "c005");
    await line(page).fill("A metal door opens behind the two on the walkway.");
    await save(page).click();
    await expect(outcome(page)).toHaveText(/Cue 3 is held: says a door; the frames show no door\./, { timeout: 15_000 });
    await expect(option(page, "c005")).toHaveAttribute("data-status", "held");
    await expect(drawer(page).getByRole("region", { name: "Why it is held" })).toContainText("says a door; the frames show no door");
    await expect(drawer(page).getByRole("listitem").filter({ hasText: "a door" })).toContainText("Not visible, cut");
    await expect(drawer(page).getByRole("button", { name: "Approve" })).toHaveAccessibleDescription("A held line can’t be approved: edit it so it is checked again.");
  });

  test("a line that breaks a writing rule is refused before any check, with what to fix", async ({ page }) => {
    await openCopy(page, "c002");
    await line(page).fill("we see wires all around the two people");
    await save(page).click();
    await expect(outcome(page)).toHaveText("This description breaks R7-person. Fix it and save again.", { timeout: 15_000 });
    await expect(drawer(page).getByRole("list", { name: "What the writing check found" })).toContainText("first/second person 'We'; describe in third person");
    await expect(drawer(page).getByRole("list", { name: "What the writing check found" })).toContainText("Must fix");
    await expect(drawer(page).getByText("Checked as")).toContainText("We see wires all around the two people.");
    // nothing changed: the line keeps its status and the draft stays for fixing
    await expect(option(page, "c002")).toHaveAttribute("data-status", "verified");
    await expect(line(page)).toHaveValue("we see wires all around the two people");
  });

  test("approve signs a line off; hold keeps it back with a reason", async ({ page }) => {
    await openCopy(page, "c001");
    const approve = drawer(page).getByRole("button", { name: "Approve" });
    await approve.click();
    await expect(outcome(page)).toHaveText("Cue 1 is approved.", { timeout: 15_000 });
    await expect(option(page, "c001")).toHaveAttribute("data-status", "approved");
    await expect(approve).toHaveAttribute("aria-disabled", "true");
    await expect(approve).toHaveAccessibleDescription("This line is approved.");
    await expect(drawer(page).getByRole("list", { name: "Who made this line" })).toContainText("Approved by a person");

    await option(page, "c005").click();
    await expect(drawer(page).getByRole("heading", { level: 2 })).toHaveText("Cue 3");
    await drawer(page).getByRole("button", { name: "Hold…" }).click();
    await drawer(page).getByRole("textbox", { name: "Why hold it? (optional)" }).fill("the walkway isn't in the frame");
    await drawer(page).getByRole("button", { name: "Hold this line" }).click();
    await expect(outcome(page)).toHaveText("Cue 3 is held: held by a reviewer: the walkway isn't in the frame. It won’t be voiced until it is edited.", { timeout: 15_000 });
    await expect(option(page, "c005")).toHaveAttribute("data-status", "held");
    await expect(page.getByRole("list", { name: "Lines by status" })).toContainText("1 held");
  });

  test("publish goes through, and the run is clean again", async ({ page }) => {
    await openCopy(page, "c001");
    await drawer(page).getByRole("button", { name: "Approve" }).click();
    await expect(outcome(page)).toHaveText("Cue 1 is approved.", { timeout: 15_000 });
    await publish(page).click();
    await expect(page.getByRole("status").filter({ hasText: /^Published/ })).toHaveText(
      "Published: the files now carry 10 lines, and coverage and WCAG are worked out again.",
      { timeout: 15_000 },
    );
    await expect(page.getByText("Changed since the last publish")).toHaveCount(0);
    await expect(publish(page)).toHaveAttribute("aria-disabled", "true");
    // the published files are fetched afresh
    await expect(page.locator("lull-player")).toHaveAttribute("descriptions", /\?v=1$/);
  });

  test("the overlap gate stops a publish: nothing is written and the lines are named", async ({ page }) => {
    await openCopy(page, "c002", "overlap");
    await drawer(page).getByRole("button", { name: "Approve" }).click();
    await expect(outcome(page)).toHaveText("Cue 2 is approved.", { timeout: 15_000 });
    await publish(page).click();
    await expect(page.getByRole("status").filter({ hasText: /Nothing was published/ })).toHaveText(
      "Nothing was published: these descriptions would overlap speech, the edge of their pause or each other.",
      { timeout: 15_000 },
    );
    await expect(option(page, "c001")).toHaveAttribute("data-blocked", "");
    await page.getByRole("button", { name: "Cue 1 at 0:09.00" }).click();
    await expect(drawer(page).getByRole("heading", { level: 2 })).toHaveText("Cue 1");
    await expect(page.getByText("Changed since the last publish")).toBeVisible();
  });

  test("a change the API refuses for the edit key is shown in its words", async ({ page }) => {
    await holdToken(page, FORK_ID, "not-the-key");
    await routeApi(page, own());
    await page.goto(`/studio/${FORK_ID}?cue=c001`);
    await drawer(page).getByRole("button", { name: "Approve" }).click();
    await expect(outcome(page)).toHaveText("Only the person who started this run can change it. Fork it to edit a copy.", { timeout: 15_000 });
    await expect(option(page, "c001")).toHaveAttribute("data-status", "verified");
  });

  test("choosing a line in the list opens it and moves the player there", async ({ page }) => {
    await openCopy(page, "c001");
    await waitReady(page);
    await page.getByRole("region", { name: "Every line" }).getByRole("button", { name: /Cue 6/ }).click();
    await expect(drawer(page).getByRole("heading", { level: 2 })).toHaveText("Cue 6");
    await expect(option(page, "c011")).toHaveAttribute("aria-selected", "true");
    await expect.poll(() => page.locator("lull-player").evaluate((el) => (el as unknown as { currentTime: number }).currentTime)).toBeCloseTo(72.459, 1);
    await expect(page).toHaveURL(/\?cue=c011$/);
  });

  test("works from the keyboard: timeline, drawer, edit and save", async ({ page }) => {
    await openCopy(page, "c001");
    const lines = page.getByRole("listbox", { name: "Lines, in time order" });
    // Tab reaches the timeline's lines at the chosen one
    // from the zoom controls ("Whole clip" is off at the whole clip), Tab lands on the chosen line
    await page.getByRole("button", { name: "Zoom in" }).focus();
    await page.keyboard.press("Tab");
    await expect(option(page, "c001")).toBeFocused();
    await expect(lines).toHaveAccessibleDescription(/arrow keys or J and K/);
    await page.keyboard.press("ArrowRight");
    await expect(option(page, "c002")).toBeFocused();
    await expect(option(page, "c002")).toHaveAttribute("aria-selected", "true");
    await expect(drawer(page).getByRole("heading", { level: 2 })).toHaveText("Cue 2");
    await page.keyboard.press("j");
    await expect(option(page, "c005")).toBeFocused();
    await page.keyboard.press("k");
    await expect(option(page, "c002")).toBeFocused();
    await page.keyboard.press("Home");
    await expect(option(page, "x001")).toBeFocused();
    await page.keyboard.press("End");
    await expect(option(page, "c013")).toBeFocused();
    // zoom from the keys
    const scale = page.getByRole("group", { name: "Zoom" });
    const before = await scale.textContent();
    await page.keyboard.press("=");
    await expect(scale).not.toHaveText(before ?? "");
    await page.keyboard.press("0");
    await expect(page.getByRole("button", { name: "Whole clip" })).toBeDisabled();
    // Enter opens the line: focus on the drawer's heading, then on to the text
    await page.keyboard.press("ArrowLeft");
    await expect(option(page, "c012")).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(drawer(page).getByRole("heading", { level: 2, name: "Cue 7" })).toBeFocused();
    for (let i = 0; i < 6 && !(await line(page).evaluate((el) => el === document.activeElement)); i++) await page.keyboard.press("Tab");
    await expect(line(page)).toBeFocused();
    await page.keyboard.press("ControlOrMeta+a");
    await page.keyboard.type("The bald person points at the wires and talks to the other.");
    await page.keyboard.press("Tab");
    await expect(save(page)).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(outcome(page)).toHaveText("Cue 7 is edited: it passed the frame check and was voiced again.", { timeout: 15_000 });
    // the button kept the focus through the check
    await expect(save(page)).toBeFocused();
  });

  test("has no serious or critical axe violations, with the drawer open, at 1440 and 390", async ({ page }) => {
    await holdToken(page, FORK_ID, FORK_TOKEN);
    await routeApi(page, own());
    for (const viewport of [{ width: 1440, height: 900 }, PHONE]) {
      await page.setViewportSize(viewport);
      await page.emulateMedia({ reducedMotion: "reduce" });
      for (const url of [`/studio/${FORK_ID}?cue=c002`, "/studio/ed90?cue=c005", `/studio/${RUN_ID}?cue=c003`]) {
        await page.goto(url, { waitUntil: "networkidle" });
        await expect(drawer(page)).toBeVisible({ timeout: 15_000 });
        await axe(page, `${url} at ${viewport.width}px`);
        const { scrollWidth, clientWidth } = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth }));
        expect(scrollWidth, `${url} at ${viewport.width}px`).toBeLessThanOrEqual(clientWidth);
      }
    }
  });
});

test.describe("the studio, read-only", () => {
  test("without the edit key, everything shows and the actions become a copy", async ({ page }) => {
    await routeApi(page, own());
    await page.goto(`/studio/${RUN_ID}?cue=c003`);
    await expect(drawer(page)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("Read-only in this browser.")).toBeVisible();
    await expect(drawer(page).getByText(/This browser doesn’t hold the edit key for this run/)).toBeVisible();
    await expect(line(page)).toHaveCount(0);
    await expect(drawer(page).getByText("A metal door swings open onto a bright white room.")).toBeVisible();
    await expect(drawer(page).getByRole("region", { name: "Why it is held" })).toContainText("says a door opens; the frames show no door");
    await expect(drawer(page).getByRole("button", { name: "Make an editable copy" })).toBeVisible();
    await expect(publish(page)).toHaveCount(0);
    await expect(page.getByText("Read-only", { exact: true })).toBeVisible();
    // an uploaded clip: no soundtrack levels to draw, its own speech and pauses
    await expect(page.locator("[class*='h_wave']")).toHaveCount(0);
    await option(page, "c001").click();
    await expect(drawer(page).getByRole("region", { name: "Writing checks" })).toContainText("R14-gender");
  });

  test("the recorded run is read-only, with the API's checks on each line", async ({ page }) => {
    await routeApi(page, own());
    await page.goto("/studio/ed90?cue=c002");
    await expect(page.getByText("Studio, recorded run")).toBeVisible();
    await expect(page.getByText("Recorded run, read-only")).toBeVisible();
    await expect(drawer(page).getByRole("listitem").filter({ hasText: "surrounded by wires" })).toContainText("Seen at 0:19.95");
    await expect(drawer(page).getByText(/A recorded run stays as it was made/)).toBeVisible();
    await expect(line(page)).toHaveCount(0);
  });

  test("the recorded run still opens with the live service down, from its own files", async ({ page }) => {
    await apiDown(page);
    await page.goto("/studio/ed90?cue=c002");
    await expect(page.getByText(/The live service didn’t answer for this run, so each line’s claims, writing checks and notes aren’t shown/)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("listbox", { name: "Lines, in time order" }).getByRole("option")).toHaveCount(10);
    // the recorded export keeps the verifier's ruling on one draft
    await expect(drawer(page).getByRole("heading", { level: 3, name: "Claims in the first draft, against the frames" })).toBeVisible();
    await expect(drawer(page).getByRole("button", { name: "Make an editable copy" })).toBeDisabled();
  });

  test("making a copy from the studio opens the copy, editable", async ({ page }) => {
    await routeApi(page, own());
    await page.goto("/studio/ed90");
    await drawer(page).getByRole("button", { name: "Make an editable copy" }).click();
    await expect(page).toHaveURL(new RegExp(`/studio/${FORK_ID}$`), { timeout: 15_000 });
    await expect(page.getByText("Studio, your run")).toBeVisible({ timeout: 15_000 });
    await expect(line(page)).toBeVisible();
    await expect(page.getByText(/This copy is kept until .*, a day after it was made\./)).toBeVisible();
  });
});

test.describe("the studio, a run that isn't there to edit", () => {
  test("still being described, not there, or the service down", async ({ page }) => {
    await routeApi(page, own());
    await page.goto(`/studio/${RUNNING_ID}`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("This run is still being described.");
    await page.goto("/studio/live-20261020-000000-gone00");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("There’s no run with that id.");
    await apiDown(page);
    await page.goto(`/studio/${FORK_ID}`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("The live service isn’t answering.");
    await routeApi(page, own());
    await page.getByRole("button", { name: "Check again" }).click();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Elephants Dream");
  });

  test("the studio's front page lists the recorded runs and this browser's runs", async ({ page }) => {
    await holdToken(page, FORK_ID, FORK_TOKEN);
    await page.goto("/studio");
    await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Studio" })).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("link", { name: /Elephants Dream/ })).toHaveAttribute("href", "/studio/ed90");
    await expect(page.getByRole("link", { name: new RegExp(FORK_ID) })).toHaveAttribute("href", `/studio/${FORK_ID}`);
  });
});
