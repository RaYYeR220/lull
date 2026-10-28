import path from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { API, RUN_ID, announcements, apiDown, recordAnnouncements, routeApi } from "./try-api";

const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1440, height: 900 };

/** The run's own pages (Tasks 4 and 6) aren't built yet; links to them are checked, not followed. */
function collectErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error" && !/ERR_CONNECTION_REFUSED|Failed to load resource/.test(msg.text())) errors.push(msg.text());
  });
  page.on("pageerror", (err) => errors.push(err.message));
  return errors;
}

async function axe(page: Page, where: string) {
  const results = await new AxeBuilder({ page }).analyze();
  const blocking = results.violations
    .filter((v) => v.impact === "moderate" || v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
  expect(blocking, where).toEqual([]);
}

async function openPicker(page: Page) {
  await page.goto("/try");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Watch a description being made.");
  await expect(page.getByText("Ready", { exact: true })).toBeVisible();
  await expect(page.getByRole("radio", { name: /Big Buck Bunny/ })).toBeChecked();
}

/** A figure in the result: the value shown for a label. */
const figure = (scope: Locator, label: string) => scope.locator("dl > div", { has: scope.page().getByRole("term").filter({ hasText: label }) }).getByRole("definition");

const start = (page: Page) => page.getByRole("button", { name: "Describe Big Buck Bunny (excerpt)" });
const run = (page: Page) => page.getByRole("region", { name: /Big Buck Bunny/ });

test.describe("try it", () => {
  test("a sample run streams stages, calls and cues to the result", async ({ page }) => {
    const errors = collectErrors(page);
    await recordAnnouncements(page);
    await routeApi(page, "run");
    await openPicker(page);

    // the cards are the API's samples, with the title, length, credit and licence it gives
    await expect(page.getByRole("radio")).toHaveCount(4 + 2); // three samples and your own clip, then the two modes
    await expect(page.getByText("Blender Foundation, peach.blender.org, CC BY 3.0")).toBeVisible();
    await expect(page.getByText("Orange Open Movie Team / Blender Foundation, elephantsdream.org, CC BY 2.5")).toBeVisible();
    await expect(page.locator("label", { hasText: "Big Buck Bunny" }).locator("time")).toHaveText("0:45");
    // Sofia is the one built-in voice: said, not offered
    await expect(page.locator("[data-voice]")).toHaveText("Voice Sofia");
    await expect(page.getByRole("combobox", { name: "Voice" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: /Watch the recorded run/ })).toHaveCount(0); // none is a recorded run here
    await expect(page.getByText("The spend check needs $0.06 of budget left for this clip.")).toBeVisible();
    await expect(page.getByText("$0.41 of $5.00")).toBeVisible();

    await page.getByRole("radio", { name: /Extended/ }).check();
    const posted = page.waitForRequest((r) => r.method() === "POST" && r.url() === `${API}/v1/runs`);
    await start(page).click();
    const form = (await posted).postData() ?? "";
    expect(form).toContain('name="sample"\r\n\r\nbbb45');
    expect(form).toContain('name="mode"\r\n\r\nextended');

    await expect(page.getByRole("heading", { level: 2, name: /Big Buck Bunny/ })).toBeFocused();
    await expect(page.getByRole("heading", { name: "Ready to watch, with 1 line held for review." })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("Finished in 1:26, for $0.0386 in model calls and 1 web search.")).toBeVisible();

    const panel = run(page);
    await expect(panel.locator("[data-stage]")).toHaveCount(7);
    await expect(panel.locator('[data-stage][data-status="done"]')).toHaveCount(7);
    await expect(panel.locator('[data-stage="listen"]')).toContainText("6.41 s");
    await expect(panel.locator('[data-stage="listen"]')).toContainText("187 words, 4 pauses");
    await expect(panel.locator('[data-stage="research"]')).toContainText("3 terms");

    // cues in time order, the held one with its reason, the timeline drawn by status
    const rows = panel.locator("li[data-cue]");
    await expect(rows).toHaveCount(4);
    await expect(rows.nth(2)).toContainText("Held");
    await expect(rows.nth(2)).toContainText("says a door opens; the frames show no door");
    await expect(rows.nth(1)).toContainText("removed “red” (not visible in the frames)");
    await expect(panel.locator('figure [data-cue][data-status="held"]')).toHaveCount(1);
    await expect(panel.locator('figure [data-cue][data-status="verified"]')).toHaveCount(3);
    await expect(panel.getByRole("figure", { name: "Timeline" })).toHaveAccessibleDescription(/^4 pauses, room for 140 words\. 41\.2 s of speech\. 4 lines so far, 1 held\.$/);
    await expect(panel.getByText("1 key moment stays undescribed: the plan drafted no line for it.")).toBeVisible();

    // every call, newest first, and the spend by model
    const calls = panel.getByRole("list", { name: "Each call, newest first" }).locator("li");
    await expect(calls).toHaveCount(11);
    await expect(calls.first()).toContainText("Verify");
    await expect(calls.first()).toContainText("Nemotron 3.5 Super VL");
    await expect(calls.last()).toContainText("18,420 in, 812 out");
    await expect(panel.getByRole("table", { name: "Spend by model" })).toContainText("Nemotron 3 Ultra");
    await expect(panel.getByText("Web lookup")).toBeVisible();
    // a web search is counted, never priced
    await expect(calls.filter({ hasText: "Web lookup" })).toContainText("search");
    await expect(calls.filter({ hasText: "Web lookup" })).not.toContainText("$");
    await expect(panel.getByText(/1 web search, not priced/)).toBeVisible();

    // the result: numbers from the done event, and where to open it
    const result = panel.getByRole("region", { name: /^Ready to watch/ });
    await expect(figure(result, "lines voiced in pauses")).toHaveText("3");
    await expect(figure(result, "held for review")).toHaveText("1");
    await expect(figure(result, "of description over speech")).toHaveText("0.00 s");
    await expect(figure(result, "of key moments described (5 of 6)")).toHaveText("83%");
    await expect(result).toContainText("WCAG 1.2.5 (AA) Needs review: 1 description held for review.");
    await expect(result.getByRole("link", { name: "Watch it described" })).toHaveAttribute("href", `/run/${RUN_ID}`);
    await expect(result.getByRole("link", { name: "Review in the studio" })).toHaveAttribute("href", `/studio/${RUN_ID}`);
    await expect(result.getByRole("link", { name: "Read the report" })).toHaveAttribute("href", `${API}/v1/runs/${RUN_ID}/files/report.html`);

    // the address and the owner token are kept for a reload and the studio
    expect(new URL(page.url()).searchParams.get("run")).toBe(RUN_ID);
    expect(await page.evaluate((id) => localStorage.getItem(`lull-edit-token:${id}`), RUN_ID)).toBe("tok-e2e-0123456789");

    // progress was announced, but not every event
    const said = await announcements(page);
    expect(said.at(-1)).toMatch(/^Finished\. 3 lines passed, 1 held\./);
    expect(said.length).toBeLessThanOrEqual(6);
    expect(errors).toEqual([]);

    // this browser's own runs: the service lists no uploads, so the page keeps them
    await page.goto("/try");
    const own = page.locator("[data-own-runs]");
    await expect(own.getByRole("link", { name: "Big Buck Bunny (excerpt)", exact: true })).toHaveAttribute("href", `/run/${RUN_ID}`);
    await expect(own).toContainText("A sample");
    await expect(own.getByRole("link", { name: /^Studio/ })).toHaveAttribute("href", `/studio/${RUN_ID}`);
  });

  test("while running, the active stage is lit and finalize shows how far it has checked", async ({ page }) => {
    await routeApi(page, "mid");
    await openPicker(page);
    await start(page).click();
    const panel = run(page);
    await expect(panel.locator("li[data-cue]")).toHaveCount(2);
    await expect(panel.locator('[data-stage="finalize"]')).toHaveAttribute("aria-current", "step");
    await expect(panel.locator('[data-stage="finalize"]')).toContainText("2 lines checked");
    await expect(panel.locator('[data-stage="publish"]')).toContainText("Waiting");
    await expect(panel.getByRole("heading", { name: /^Describing Big Buck Bunny \(excerpt\)$/ })).toBeVisible();
    await expect(page.getByRole("button", { name: "Describe another clip" })).toHaveCount(0);
    for (const viewport of [DESKTOP, PHONE]) {
      await page.setViewportSize(viewport);
      await axe(page, `mid-run at ${viewport.width}px`);
    }
  });

  const REFUSALS = [
    { scenario: "402", heading: "Today’s model budget for live runs is spent.", body: "Recorded runs still work.", retry: false },
    { scenario: "409", heading: "Another description is running.", body: "Try again in a minute", retry: true },
    { scenario: "413", heading: "This file is too large.", body: "files up to 30 MB", retry: false },
    { scenario: "422-format", heading: "Lull can’t take this file type.", body: "Send an mp4, mov, webm or mkv video.", retry: false },
    { scenario: "422-too_long", heading: "This video is too long.", body: "This video is 61.5 s long.", retry: false },
    { scenario: "422-unreadable", heading: "This file couldn’t be read as a video.", body: "could not be read as a video", retry: false },
    { scenario: "429", heading: "You’ve reached the live limit for now.", body: "Try again in 52 minutes", retry: true },
    { scenario: "503", heading: "Other uploads are being checked.", body: "Try again in a few seconds.", retry: true },
    { scenario: "408", heading: "The upload took too long to arrive.", body: "Try a smaller file or a faster connection.", retry: false },
    { scenario: "429-one_upload", heading: "Your last upload is still arriving.", body: "Wait for it to finish.", retry: true },
  ];

  for (const r of REFUSALS) {
    test(`a ${r.scenario} refusal says so in plain words`, async ({ page }) => {
      await routeApi(page, r.scenario);
      await openPicker(page);
      await start(page).click();
      const heading = page.getByRole("heading", { level: 2, name: r.heading });
      await expect(heading).toBeFocused();
      const notice = page.getByRole("region", { name: r.heading });
      await expect(notice).toContainText(r.body);
      await expect(notice.getByRole("link", { name: "Elephants Dream" })).toHaveAttribute("href", "/watch/ed90");
      await expect(notice.getByRole("button", { name: "Try again" })).toHaveCount(r.retry ? 1 : 0);
      // the picker is still there to change the choice
      await expect(start(page)).toBeEnabled();
    });
  }

  test("a busy refusal counts down its Retry-After, then trying again works", async ({ page }) => {
    await routeApi(page, "409");
    await openPicker(page);
    await start(page).click();
    const notice = page.getByRole("region", { name: "Another description is running." });
    const again = notice.getByRole("button", { name: "Try again" });
    await expect(notice.getByText(/^You can try again in 0:0[12]\.$/)).toBeVisible();
    await expect(again).toBeDisabled();
    await expect(notice.getByText("You can try again now.")).toBeVisible({ timeout: 5000 });
    await expect(again).toBeEnabled();
    await expect(page.getByRole("status")).toHaveText("You can try again now."); // said once, not every second
    await routeApi(page, "run");
    await again.click();
    await expect(page.getByRole("heading", { name: /^Ready to watch/ })).toBeVisible({ timeout: 15_000 });
  });

  test("a long rate-limit wait reads in hours and minutes, not seconds", async ({ page }) => {
    await routeApi(page, "429");
    await openPicker(page);
    await start(page).click();
    await expect(page.getByText(/^You can try again in 5[12]:\d\d\.$/)).toBeVisible();
  });

  test("with the API down, the page explains and offers the recorded runs", async ({ page }) => {
    const errors = collectErrors(page);
    await apiDown(page);
    await page.goto("/try");
    await expect(page.getByText("Not answering")).toBeVisible();
    const notice = page.getByRole("region", { name: "The live service isn’t answering." });
    await expect(notice.getByRole("link", { name: "Elephants Dream" })).toHaveAttribute("href", "/watch/ed90");
    await expect(page.getByRole("button", { name: "Describe your clip" })).toBeDisabled();
    await expect(page.getByText("The live service isn’t answering, so a description can’t start.")).toBeVisible();
    // checking again once it answers brings the picker back
    await routeApi(page, "run");
    await notice.getByRole("button", { name: "Check again" }).click();
    await expect(start(page)).toBeEnabled();
    expect(errors).toEqual([]);
  });

  test("with today's budget spent, start is held back with the reason", async ({ page }) => {
    await routeApi(page, "spent");
    await page.goto("/try");
    await expect(page.getByText("$5.00 of $5.00")).toBeVisible();
    await expect(start(page)).toBeDisabled();
    await expect(page.getByText(/Today’s model budget hasn’t \$0\.06 of room left/)).toBeVisible();
  });

  test("an upload is checked here first: type, size, then length", async ({ page }) => {
    await routeApi(page, "run");
    await openPicker(page);
    // before any upload: where the clip goes, who can see the run, and how long it is kept
    const privacy = page.locator("[data-privacy]");
    await expect(privacy).toContainText("sent to Nebius Token Factory, where NVIDIA Nemotron models");
    await expect(privacy).toContainText("Anyone with the finished run’s link can open it");
    await expect(privacy).toContainText("Uploads are deleted after 7 days, and copies made in the studio after 24 hours.");
    await expect(page.locator('input[type="file"]')).toHaveAccessibleDescription(/Where your clip goes/);
    const input = page.locator('input[type="file"]');
    await input.setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("hello") });
    await expect(page.getByRole("radio", { name: /Your own clip/ })).toBeChecked();
    await expect(page.getByText("This isn’t a video Lull takes. Choose an mp4, mov, webm or mkv file.").first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Describe your clip" })).toBeDisabled();

    await input.setInputFiles({ name: "big.mp4", mimeType: "video/mp4", buffer: Buffer.alloc(31 * 1024 * 1024) });
    await expect(page.getByText(/This file is 31 MB\. Live descriptions take files up to 30 MB/).first()).toBeVisible();

    await input.setInputFiles(path.join(__dirname, "fixtures", "video.mp4"));
    await expect(page.locator("p[data-status=ready]")).toContainText(/0:0\d long/);
    const posted = page.waitForRequest((r) => r.method() === "POST");
    await page.getByRole("button", { name: "Describe your clip" }).click();
    expect((await posted).postData()).toContain('filename="video.mp4"');
    await expect(page.getByRole("heading", { name: /^Ready to watch/ })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("heading", { level: 2, name: "Described video.mp4" })).toBeVisible();
  });

  test("a run that stops says where and why", async ({ page }) => {
    await routeApi(page, "fail");
    await openPicker(page);
    await start(page).click();
    const notice = page.getByRole("region", { name: "Today’s model budget for live runs is spent." });
    await expect(notice).toContainText("Recorded runs still work.");
    await expect(page.locator('[data-stage="plan"]')).toHaveAttribute("data-status", "failed");
    await expect(page.locator('[data-stage="plan"]')).toContainText("Stopped");
    await notice.getByRole("button", { name: "Describe another clip" }).click();
    await expect(page.getByRole("heading", { name: "Choose a clip" })).toBeFocused();
    expect(new URL(page.url()).searchParams.get("run")).toBeNull();
  });

  test("a stream cut off mid-run is followed to its end", async ({ page }) => {
    await routeApi(page, "drop");
    await openPicker(page);
    await start(page).click();
    await expect(page.getByRole("heading", { name: /^Ready to watch/ })).toBeVisible({ timeout: 15_000 });
    // seen once each, though the follow replayed from the start
    await expect(page.locator('[data-stage="listen"]')).toHaveAttribute("data-status", "done");
    await expect(page.locator("li[data-cue]")).toHaveCount(4);
    await expect(page.getByRole("list", { name: "Each call, newest first" }).locator("li")).toHaveCount(11);
  });

  test("a reload follows the run in the address", async ({ page }) => {
    await routeApi(page, "run");
    await page.goto(`/try?run=${RUN_ID}`);
    await expect(page.getByRole("heading", { name: "Ready to watch, with 1 line held for review." })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("Finished in 1:26, for $0.0386 in model calls and 1 web search.")).toBeVisible();
    await expect(page.getByRole("heading", { level: 2, name: `Described run ${RUN_ID}` })).toBeVisible();
    await expect(page.locator("li[data-cue]")).toHaveCount(4);
  });

  test("an unknown run in the address says so", async ({ page }) => {
    await routeApi(page, "run");
    await page.goto("/try?run=live-nope");
    await expect(page.getByRole("heading", { name: "There’s no run with that id." })).toBeVisible({ timeout: 15_000 });
  });

  test("the whole flow works from the keyboard", async ({ page }) => {
    await routeApi(page, "run");
    await openPicker(page);
    await page.getByRole("heading", { name: "Choose a clip" }).focus();
    await page.keyboard.press("Tab");
    await expect(page.getByRole("radio", { name: /Big Buck Bunny/ })).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(page.getByRole("radio", { name: /Elephants Dream \(excerpt\)/ })).toBeChecked();
    await page.keyboard.press("ArrowUp");
    await expect(page.getByRole("radio", { name: /Big Buck Bunny/ })).toBeChecked();
    // Tab on to the start door
    for (let i = 0; i < 12; i++) {
      await page.keyboard.press("Tab");
      if (await start(page).evaluate((el) => el === document.activeElement)) break;
    }
    await expect(start(page)).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { level: 2, name: /Big Buck Bunny/ })).toBeFocused();
    await expect(page.getByRole("heading", { name: /^Ready to watch/ })).toBeVisible({ timeout: 15_000 });
    for (let i = 0; i < 6; i++) {
      await page.keyboard.press("Tab");
      if (await page.getByRole("link", { name: "Watch it described" }).evaluate((el) => el === document.activeElement)) break;
    }
    await expect(page.getByRole("link", { name: "Watch it described" })).toBeFocused();
  });

  test("no moderate, serious or critical axe violations: picker, done and refusal, at 1440 and 390", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    for (const viewport of [DESKTOP, PHONE]) {
      await page.setViewportSize(viewport);
      await routeApi(page, "run");
      await openPicker(page);
      await axe(page, `picker at ${viewport.width}px`);
      await start(page).click();
      await expect(page.getByRole("heading", { name: /^Ready to watch/ })).toBeVisible({ timeout: 15_000 });
      await axe(page, `done at ${viewport.width}px`);
      await routeApi(page, "409");
      await page.goto("/try");
      await expect(start(page)).toBeEnabled();
      await start(page).click();
      await expect(page.getByRole("heading", { name: "Another description is running." })).toBeVisible();
      await axe(page, `refusal at ${viewport.width}px`);
    }
  });

  test("no horizontal scroll at 390px while running or done", async ({ page }) => {
    await page.setViewportSize(PHONE);
    await routeApi(page, "run");
    await openPicker(page);
    await start(page).click();
    await expect(page.getByRole("heading", { name: /^Ready to watch/ })).toBeVisible({ timeout: 15_000 });
    const { scrollWidth, clientWidth, spill } = await page.evaluate(() => {
      const root = document.documentElement;
      const inScroller = (el: Element) => {
        for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
          const o = getComputedStyle(p).overflowX;
          if (o === "auto" || o === "scroll" || o === "hidden") return true;
        }
        return false;
      };
      const spill = [...document.querySelectorAll("main a, main button, main h1, main h2, main h3, main p, main li, main dd, main td")]
        .filter((el) => {
          const r = el.getBoundingClientRect();
          return r.width > 0 && (r.right > root.clientWidth + 1 || r.left < -1) && !inScroller(el);
        })
        .map((el) => `${el.tagName.toLowerCase()}: ${(el.textContent ?? "").trim().slice(0, 40)}`);
      return { scrollWidth: root.scrollWidth, clientWidth: root.clientWidth, spill };
    });
    expect(scrollWidth).toBeLessThanOrEqual(clientWidth);
    expect(spill).toEqual([]);
  });

  test("reduced motion: nothing slides, pulses or sweeps", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await routeApi(page, "mid");
    await openPicker(page);
    await start(page).click();
    await expect(page.locator("li[data-cue]")).toHaveCount(2);
    const moving = await page.evaluate(() =>
      [...document.querySelectorAll("main *")]
        .filter((el) => getComputedStyle(el).animationName !== "none" || getComputedStyle(el, "::after").animationName !== "none")
        .map((el) => el.className),
    );
    expect(moving).toEqual([]);
  });
});
