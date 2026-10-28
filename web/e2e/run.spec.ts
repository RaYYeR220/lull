import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { API, FAILED_ID, FAILED_TOKEN, FORK_ID, RUN_ID, RUN_TOKEN, RUNNING_ID, RUNNING_TOKEN, STOPPED_ID, STOPPED_TOKEN, apiDown, holdToken, own, routeApi } from "./try-api";

/**
 * A finished live run's page, /run/<id>: loaded from the API (the scripted stand-in,
 * e2e/mock-api.mjs and e2e/mock-runs.mjs) into the same player, script, checks and files as a
 * recorded run's page; or, when it isn't published, where it stands.
 */
const PHONE = { width: 390, height: 844 };
const player = (page: Page) => page.locator("lull-player");

function pageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(err.message));
  page.on("console", (msg) => {
    // the player asks for recordings that a run may not have; a 404 there is expected
    if (msg.type() === "error" && !/Failed to load resource|ERR_CONNECTION_REFUSED/.test(msg.text())) errors.push(msg.text());
  });
  return errors;
}

test.describe("a finished live run", () => {
  test("plays the run's own files from the API, with its script, checks and files", async ({ page }) => {
    const errors = pageErrors(page);
    await routeApi(page, own("run"));
    await page.goto(`/run/${RUN_ID}`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("corridor-walk.mp4");
    await expect(page.getByText("Live run, finished", { exact: true })).toBeVisible();
    await expect(page.getByText(/Described by a live run, published .*2026/)).toBeVisible();

    // an uploaded clip: the original video the API keeps for it, with the descriptions over it,
    // so every mode is offered (its sound isn't the described mix)
    const p = player(page);
    await expect(p).toHaveAttribute("src", `${API}/v1/runs/${RUN_ID}/original`);
    expect(await p.evaluate((el) => el.hasAttribute("sound"))).toBe(false);
    await expect(p).toHaveAttribute("descriptions", `${API}/v1/runs/${RUN_ID}/files/descriptions.vtt`);
    await expect(p).toHaveAttribute("described-audio", `${API}/v1/runs/${RUN_ID}/files/described-audio.m4a`);
    await expect(p).toHaveAttribute("crossorigin", "anonymous");
    await expect(p).toHaveAttribute("cue-audio-base", `${API}/v1/runs/${RUN_ID}/cues/{id}/audio`);
    // the original's own sound: every mode plays, spoken cues too
    await expect.poll(() => p.evaluate((el) => (el as unknown as { mode: string | null }).mode), { timeout: 15_000 }).toBe("mixed");
    await p.evaluate((el) => ((el as unknown as { mode: string }).mode = "spoken"));
    expect(await p.evaluate((el) => (el as unknown as { mode: string | null }).mode)).toBe("spoken");
    await p.getByRole("button", { name: /Description mode/ }).click();
    await expect(p.getByRole("radio", { name: /Spoken cues/ })).toBeEnabled();
    await page.keyboard.press("Escape");

    // the script, in time order, the held line included
    const lines = page.locator("[data-line]");
    await expect(lines).toHaveCount(4);
    await expect(lines.nth(0)).toContainText("Cue 1");
    await expect(lines.nth(0)).toContainText("In a dim corridor of cables");
    await expect(page.locator('[data-line="c003"]')).toHaveAttribute("data-status", "held");
    await expect(page.locator('[data-line="c004"]')).toContainText("9 of 9 words");

    const checks = page.getByRole("region", { name: "What was checked" });
    await expect(checks).toContainText("Over speech");
    await expect(checks).toContainText("3 of 3");
    await expect(checks).toContainText("3 of 5 pauses carry a line");
    await expect(checks).toContainText("Passed the frame check");
    await expect(checks).toContainText("3 of 4");
    await expect(checks).toContainText("$0.02");

    const files = page.getByRole("region", { name: "Files from this run" }).getByRole("link");
    await expect(files).toHaveText(["descriptions.vtt", "extended.vtt", "described-audio.m4a", "described.mp4", "script.md", "report.html", "cues.json"]);
    await expect(files.first()).toHaveAttribute("href", `${API}/v1/runs/${RUN_ID}/files/descriptions.vtt`);

    // not this browser's run: a copy can be made, and the studio opens to look
    await expect(page.getByRole("button", { name: "Make an editable copy" })).toBeEnabled();
    await expect(page.getByRole("link", { name: "Open in Studio" })).toHaveAttribute("href", `/studio/${RUN_ID}`);
    expect(errors).toEqual([]);
  });

  test("with the run's edit key, it offers the studio to edit", async ({ page }) => {
    await holdToken(page, RUN_ID, RUN_TOKEN);
    await routeApi(page, own("run"));
    await page.goto(`/run/${RUN_ID}`);
    await expect(page.getByText("This browser holds the edit key for this run.", { exact: false })).toBeVisible();
    await expect(page.getByRole("link", { name: "Review in the studio" })).toHaveAttribute("href", `/studio/${RUN_ID}`);
    await expect(page.getByRole("button", { name: "Make an editable copy" })).toHaveCount(0);
  });

  test("a run of a recorded clip plays the original video and credits it", async ({ page }) => {
    await routeApi(page, own("run"));
    await page.goto(`/run/${FORK_ID}`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Elephants Dream");
    await expect(page.getByRole("link", { name: /CC BY 2\.5/ })).toBeVisible();
    // the API's own copy of the video
    await expect(player(page)).toHaveAttribute("src", `${API}/v1/runs/${FORK_ID}/original`);
    await expect(player(page)).not.toHaveAttribute("sound", /./);
    await expect(page.locator("[data-line]")).toHaveCount(13);
  });

  test("says when it is loading", async ({ page }) => {
    await routeApi(page, own("run"));
    let release: () => void = () => {};
    const held = new Promise<void>((r) => (release = r));
    await page.route(`${API}/v1/runs/${RUN_ID}`, async (route) => {
      await held;
      await route.continue({ url: route.request().url().replace(API, `http://127.0.0.1:${process.env.MOCK_API_PORT ?? 3218}/run`) });
    });
    await page.goto(`/run/${RUN_ID}`);
    await expect(page.getByRole("status").filter({ hasText: "Loading the run…" })).toBeVisible();
    release();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("corridor-walk.mp4");
  });

  test("a run still being described: only the browser that started it may follow it live", async ({ page }) => {
    await routeApi(page, own("run"));
    await page.goto(`/run/${RUNNING_ID}`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("This run is still being described.");
    await expect(page.getByRole("link", { name: "Follow it live" })).toHaveCount(0);
    await expect(page.getByText("Only the browser that started it can follow it as it happens.")).toBeVisible();
    await holdToken(page, RUNNING_ID, RUNNING_TOKEN);
    await page.goto(`/run/${RUNNING_ID}`);
    await expect(page.getByRole("link", { name: "Follow it live" })).toHaveAttribute("href", `/try?run=${RUNNING_ID}`);
  });

  test("a run that stopped says why, in its own words, to the browser that started it", async ({ page }) => {
    await routeApi(page, own("run"));
    await page.goto(`/run/${FAILED_ID}`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("This run stopped before it finished.");
    await expect(page.getByText("Only the browser that started it can see why it stopped.")).toBeVisible();
    await expect(page.getByRole("link", { name: "Describe a clip" })).toHaveAttribute("href", "/try");
    await holdToken(page, FAILED_ID, FAILED_TOKEN);
    await page.goto(`/run/${FAILED_ID}`);
    await expect(page.getByText("The speech server didn’t answer in time, so the run stopped at listen.")).toBeVisible();
  });

  test("an unfinished run says so; only the browser that started it may resume it, with its key", async ({ page }) => {
    await routeApi(page, own("run"));
    await page.goto(`/run/${STOPPED_ID}`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("This run hasn’t finished.");
    await expect(page.getByRole("link", { name: "Resume" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Describe a clip" })).toHaveAttribute("href", "/try");

    // the owner: the detail is asked for with the edit key, and the door resumes it
    const asked: (string | undefined)[] = [];
    page.on("request", (r) => {
      if (r.url() === `${API}/v1/runs/${STOPPED_ID}`) asked.push(r.headers()["authorization"]);
    });
    await holdToken(page, STOPPED_ID, STOPPED_TOKEN);
    await page.goto(`/run/${STOPPED_ID}`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Stopped: you left before it finished.");
    await expect(page.getByRole("link", { name: "Resume" })).toHaveAttribute("href", `/try?run=${STOPPED_ID}`);
    expect(asked).toContain(`Bearer ${STOPPED_TOKEN}`);
  });

  test("an id that isn't there, or was removed, says so", async ({ page }) => {
    await routeApi(page, own("run"));
    await page.goto("/run/live-20261020-000000-gone00");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("There’s no run with that id.");
    await expect(page.getByText(/copies made for editing are kept for a day/)).toBeVisible();
  });

  test("with the live service down it says so, and loads once it answers", async ({ page }) => {
    await apiDown(page);
    await page.goto(`/run/${RUN_ID}`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("The live service isn’t answering.");
    await routeApi(page, own("run"));
    await page.getByRole("button", { name: "Check again" }).click();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("corridor-walk.mp4");
  });

  test("try-it's result opens this page", async ({ page }) => {
    await routeApi(page, "run");
    await page.goto(`/try?run=${RUN_ID}`);
    const door = page.getByRole("link", { name: "Watch it described" });
    await expect(door).toHaveAttribute("href", `/run/${RUN_ID}`, { timeout: 15_000 });
    await door.click();
    await expect(page).toHaveURL(new RegExp(`/run/${RUN_ID}$`));
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("corridor-walk.mp4");
  });

  test("has no moderate, serious or critical axe violations, and no sideways scroll on a phone", async ({ page }) => {
    // several whole-page axe passes: slow when the suite runs in parallel
    test.setTimeout(180_000);
    for (const viewport of [{ width: 1440, height: 900 }, PHONE]) {
      await page.setViewportSize(viewport);
      await page.emulateMedia({ reducedMotion: "reduce" });
      await routeApi(page, own("run"));
      for (const id of [RUN_ID, FAILED_ID]) {
        await page.goto(`/run/${id}`, { waitUntil: "networkidle" });
        await expect(page.getByRole("heading", { level: 1 })).not.toHaveText("");
        await expect(page.getByRole("status").filter({ hasText: "Loading" })).toHaveCount(0);
        const results = await new AxeBuilder({ page }).analyze();
        const blocking = results.violations
          .filter((v) => v.impact === "moderate" || v.impact === "serious" || v.impact === "critical")
          .map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
        expect(blocking, `${id} at ${viewport.width}px`).toEqual([]);
        const { scrollWidth, clientWidth } = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth }));
        expect(scrollWidth, `${id} at ${viewport.width}px`).toBeLessThanOrEqual(clientWidth);
      }
    }
  });
});

test.describe("following a run with its edit key", () => {
  test("a resumed run is followed with the owner's token as a Bearer", async ({ page }) => {
    const sent: (string | undefined)[] = [];
    page.on("request", (r) => {
      if (r.url().startsWith(`${API}/v1/runs/${RUN_ID}/events`)) sent.push(r.headers()["authorization"]);
    });
    await holdToken(page, RUN_ID, RUN_TOKEN);
    await routeApi(page, own("resume"));
    await page.goto(`/try?run=${RUN_ID}`);
    // the mock refuses to resume without the token: reaching the end proves it was sent
    await expect(page.getByRole("heading", { name: "Ready to watch, with 1 line held for review." })).toBeVisible({ timeout: 15_000 });
    expect(sent.length).toBeGreaterThan(0);
    expect(sent.every((h) => h === `Bearer ${RUN_TOKEN}`)).toBe(true);
  });

  test("without the token, the API's refusal is shown", async ({ page }) => {
    await routeApi(page, own("resume"));
    await page.goto(`/try?run=${RUN_ID}`);
    await expect(page.getByText("Only the person who started this run can resume it.")).toBeVisible({ timeout: 15_000 });
  });
});
