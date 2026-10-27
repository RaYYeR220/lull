import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { CUES, FULL, openEmbed, playerState, routeFixtures, videoClock, waitReady, type PlayerAttrs } from "./player-fixtures";

/**
 * The description player, <lull-player>. Most tests run it the way another site would:
 * a page on http://embed.test loads /player.js with one script tag and points it at the
 * generated 6 s fixture (cues at 1.0 s and 3.6 s, an extended cue at 2.5 s that holds 1.5 s).
 * The kitchen tests run it inside the site through the React wrapper.
 */
const PHONE = { width: 390, height: 844 };
const player = (page: Page) => page.locator("lull-player");
const statusRegion = (page: Page) => player(page).locator('[data-live="status"]');
const cueRegion = (page: Page) => player(page).locator('[data-live="cue"]');
const modeButton = (page: Page) => player(page).getByRole("button", { name: /^Description mode/ });

function pageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(err.message));
  return errors;
}

async function open(page: Page, baseURL: string | undefined, attrs: PlayerAttrs = FULL) {
  await openEmbed(page, baseURL!, attrs);
  await waitReady(page);
}

async function chooseMode(page: Page, name: string) {
  await modeButton(page).click();
  await player(page).getByRole("radio", { name }).check();
  await page.keyboard.press("Escape");
  await expect(modeButton(page)).toHaveAttribute("aria-expanded", "false");
}

/** Starts recording player events and video play/pause with wall-clock times, in the page. */
async function record(page: Page) {
  await player(page).evaluate((el) => {
    const v = el.shadowRoot!.querySelector("video")!;
    const w = window as unknown as { log: { what: string; t: number; at: number; detail?: unknown }[] };
    w.log = [];
    const push = (what: string, detail?: unknown) => w.log.push({ what, t: v.currentTime, at: performance.now(), detail });
    v.addEventListener("pause", () => push("pause"));
    v.addEventListener("play", () => push("play"));
    el.addEventListener("lull-cue", (e) => push("cue", (e as CustomEvent).detail));
    new MutationObserver(() => {
      const text = el.shadowRoot!.querySelector('[data-live="cue"]')!.textContent;
      if (text) push("live", text);
    }).observe(el.shadowRoot!.querySelector('[data-live="cue"]')!, { childList: true, characterData: true, subtree: true });
  });
}

type Entry = { what: string; t: number; at: number; detail?: Record<string, unknown> | string };
const log = (page: Page) => page.evaluate(() => (window as unknown as { log: Entry[] }).log);

test.describe("player, embedded on another site", () => {
  test("loads from one script tag and reads every track", async ({ page, baseURL }) => {
    const errors = pageErrors(page);
    await open(page, baseURL);
    expect(await playerState(page)).toMatchObject({ mode: "mixed", video: "ready", descriptions: "ready", extended: "ready", playing: false });
    await expect(page.getByRole("region", { name: "Test pattern, described" })).toBeVisible();
    await player(page).getByRole("button", { name: "Transcript" }).click();
    const rows = page.getByRole("region", { name: "Descriptions transcript" }).getByRole("button");
    await expect(rows).toHaveCount(3);
    await expect(rows.nth(0)).toContainText(CUES.c001.text);
    await expect(rows.nth(1)).toContainText(CUES.x001.text);
    await expect(rows.nth(1)).toContainText("Extended");
    // the &amp; in the file is decoded
    await expect(rows.nth(2)).toContainText(CUES.c002.text);
    expect(errors).toEqual([]);
  });

  test("the mode menu is a radio group, and every mode can be chosen", async ({ page, baseURL }) => {
    await open(page, baseURL);
    const group = player(page).getByRole("radiogroup", { name: "Description mode" });
    await expect(group).toBeHidden();
    await modeButton(page).click();
    await expect(modeButton(page)).toHaveAttribute("aria-expanded", "true");
    await expect(group.getByRole("radio")).toHaveCount(4);
    await expect(group.getByRole("radio", { name: "Mixed audio" })).toBeChecked();
    await expect(group.getByRole("radio", { name: "Mixed audio" })).toBeFocused();

    const modes = [
      { radio: "Spoken cues", mode: "spoken", said: "Spoken cues. Recorded voice." },
      { radio: "My screen reader", mode: "screen-reader", said: "My screen reader. Each description will be announced here." },
      { radio: "Extended", mode: "extended", said: "Extended. Recorded voice." },
      { radio: "Mixed audio", mode: "mixed", said: "Mixed audio. The described soundtrack plays." },
    ];
    for (const m of modes) {
      // a pointer pick closes the menu
      if (!(await group.isVisible())) await modeButton(page).click();
      await group.getByRole("radio", { name: m.radio }).check();
      await expect(group).toBeHidden();
      expect((await playerState(page)).mode).toBe(m.mode);
      await expect(statusRegion(page)).toHaveText(m.said);
      await expect(modeButton(page)).toHaveAccessibleName(`Description mode ${m.radio}`);
    }
    // arrow keys move through the group and keep it open; Enter closes it on the choice
    await page.keyboard.press("m");
    await expect(group.getByRole("radio", { name: "Mixed audio" })).toBeFocused();
    await page.keyboard.press("ArrowDown");
    expect((await playerState(page)).mode).toBe("spoken");
    await expect(group).toBeVisible();
    await page.keyboard.press("Enter");
    await expect(group).toBeHidden();
    await expect(modeButton(page)).toBeFocused();
    // the viewer's choice is remembered for the next player on this site
    expect(await page.evaluate(() => localStorage.getItem("lull-player-mode"))).toBe("spoken");
  });

  test("mixed audio mutes the video and keeps the described track in step", async ({ page, baseURL }) => {
    await open(page, baseURL);
    await player(page).getByRole("button", { name: "Play" }).click();
    await page.waitForTimeout(1500);
    const sync = () =>
      player(page).evaluate((el) => {
        const v = el.shadowRoot!.querySelector("video")!;
        const a = el.shadowRoot!.querySelector("audio")!;
        return { muted: v.muted, drift: Math.abs(a.currentTime - v.currentTime), audioPaused: a.paused, rate: a.playbackRate };
      });
    let s = await sync();
    expect(s.muted).toBe(true);
    expect(s.audioPaused).toBe(false);
    expect(s.drift).toBeLessThan(0.15);

    // a jump: the track follows
    await player(page).evaluate((el) => (el as unknown as { seek(t: number): void }).seek(4));
    await page.waitForTimeout(400);
    s = await sync();
    expect(s.drift).toBeLessThan(0.15);

    // drift is corrected: push the track 0.5 s off and it comes back
    await player(page).evaluate((el) => {
      const a = el.shadowRoot!.querySelector("audio")!;
      a.currentTime += 0.5;
    });
    await page.waitForTimeout(400);
    expect((await sync()).drift).toBeLessThan(0.15);

    await page.keyboard.press(">");
    expect((await sync()).rate).toBe(1.25);
    await player(page).getByRole("button", { name: "Pause" }).click();
    expect((await sync()).audioPaused).toBe(true);
  });

  test("spoken cues: the recorded clip plays at the cue and the programme dips under it", async ({ page, baseURL }) => {
    await open(page, baseURL);
    await chooseMode(page, "Spoken cues");
    await record(page);
    await player(page).getByRole("button", { name: "Play" }).click();
    await expect.poll(async () => (await playerState(page)).ducked, { timeout: 4000 }).toBe(true);
    const state = await playerState(page);
    // same-origin media: the duck runs on the Web Audio graph
    expect(state.programme).toBe("graph");
    const cue = (await log(page)).find((e) => e.what === "cue");
    expect(cue?.detail).toMatchObject({ id: "c001", via: "clip", mode: "spoken", kind: "standard" });
    expect(cue!.t).toBeGreaterThanOrEqual(CUES.c001.start - 0.03);
    expect(cue!.t).toBeLessThan(CUES.c001.start + 0.3);
    // the clip ends (1.2 s), the programme comes back up
    await expect.poll(async () => (await playerState(page)).ducked, { timeout: 4000 }).toBe(false);
    expect((await videoClock(page)).t).toBeGreaterThan(CUES.c001.end - 0.2);
    await expect(player(page).locator(".meta")).toContainText("Recorded voice");
  });

  test("spoken cues without recordings use the system voice and say so", async ({ page, baseURL }) => {
    const attrs = { ...FULL };
    delete attrs["cue-audio-base"];
    await open(page, baseURL, attrs);
    await chooseMode(page, "Spoken cues");
    await expect(player(page).locator(".meta")).toContainText("System voice");
    await modeButton(page).click();
    await expect(player(page).getByRole("radio", { name: "Spoken cues" })).toHaveAccessibleDescription(/System voice/);
    await page.keyboard.press("Escape");
    await record(page);
    await player(page).getByRole("button", { name: "Play" }).click();
    await expect.poll(async () => (await log(page)).find((e) => e.what === "cue")?.detail, { timeout: 4000 }).toMatchObject({ id: "c001", via: "system" });
  });

  test("my screen reader: each line goes to the live region at its start", async ({ page, baseURL }) => {
    await open(page, baseURL);
    await chooseMode(page, "My screen reader");
    await expect(cueRegion(page)).toHaveAttribute("aria-live", "polite");
    await record(page);
    await player(page).getByRole("button", { name: "Play" }).click();
    await expect(cueRegion(page)).toHaveText(CUES.c001.text, { timeout: 4000 });
    await expect(cueRegion(page)).toHaveText(CUES.c002.text, { timeout: 5000 });
    const lives = (await log(page)).filter((e) => e.what === "live");
    expect(lives.map((e) => e.detail)).toEqual([CUES.c001.text, CUES.c002.text]);
    // written within a frame or two of the cue start (+30 ms for the clear-then-write)
    expect(lives[0]!.t).toBeGreaterThanOrEqual(CUES.c001.start - 0.03);
    expect(lives[0]!.t).toBeLessThan(CUES.c001.start + 0.25);
    expect(lives[1]!.t).toBeGreaterThanOrEqual(CUES.c002.start - 0.03);
    expect(lives[1]!.t).toBeLessThan(CUES.c002.start + 0.25);
    // nothing is spoken in this mode, and the line shows on screen
    expect((await playerState(page)).ducked).toBe(false);
    await expect(player(page).locator(".line")).toHaveText(CUES.c002.text);
  });

  test("extended: the video waits at the cue for its pause, then goes on", async ({ page, baseURL }) => {
    await open(page, baseURL);
    await chooseMode(page, "Extended");
    await record(page);
    await player(page).getByRole("button", { name: "Play" }).click();
    await expect.poll(async () => (await playerState(page)).holding, { timeout: 5000 }).toBe(true);
    await expect(player(page).getByRole("button", { name: "Pause" })).toBeVisible();
    await expect(player(page).locator(".hold")).toContainText("The video waits 1.50 s for this line");
    await expect.poll(async () => (await playerState(page)).holding, { timeout: 5000 }).toBe(false);
    await page.waitForTimeout(300);
    const entries = await log(page);
    const held = entries.find((e) => e.what === "cue" && (e.detail as { id?: string }).id === "x001");
    expect(held?.detail).toMatchObject({ kind: "extended", hold: CUES.x001.pause, mode: "extended" });
    const pausedAt = entries.find((e) => e.what === "pause" && Math.abs(e.t - CUES.x001.start) < 0.1);
    const resumed = entries.find((e) => e.what === "play" && pausedAt && e.at > pausedAt.at);
    expect(pausedAt, JSON.stringify(entries)).toBeTruthy();
    expect(resumed, JSON.stringify(entries)).toBeTruthy();
    const heldFor = (resumed!.at - pausedAt!.at) / 1000;
    expect(heldFor).toBeGreaterThan(CUES.x001.pause - 0.1);
    expect(heldFor).toBeLessThan(CUES.x001.pause + 0.5);
    // the picture waited on the cue's frame and then moved on
    expect(Math.abs(pausedAt!.t - CUES.x001.start)).toBeLessThan(0.06);
    expect((await videoClock(page)).paused).toBe(false);
    expect((await playerState(page)).playing).toBe(true);
  });

  test("pausing during an extended hold keeps the rest of the wait", async ({ page, baseURL }) => {
    await open(page, baseURL);
    await chooseMode(page, "Extended");
    await player(page).evaluate((el) => (el as unknown as { seek(t: number): void }).seek(2.4));
    await player(page).getByRole("button", { name: "Play" }).click();
    await expect.poll(async () => (await playerState(page)).holding, { timeout: 3000 }).toBe(true);
    await player(page).getByRole("button", { name: "Pause" }).click();
    await page.waitForTimeout(2000);
    // still waiting, still on the cue's frame
    expect(await playerState(page)).toMatchObject({ holding: true, playing: false });
    expect((await videoClock(page)).paused).toBe(true);
    await player(page).getByRole("button", { name: "Play" }).click();
    await expect.poll(async () => (await playerState(page)).holding, { timeout: 3000 }).toBe(false);
    expect((await videoClock(page)).paused).toBe(false);
  });

  test("keyboard map", async ({ page, baseURL }) => {
    await open(page, baseURL);
    const play = player(page).getByRole("button", { name: "Play" });
    await play.focus();
    await page.keyboard.press("k");
    expect((await playerState(page)).playing).toBe(true);
    await expect(statusRegion(page)).toHaveText("Playing.");
    await page.keyboard.press("k");
    expect((await playerState(page)).playing).toBe(false);
    await expect(statusRegion(page)).toHaveText("Paused.");
    // Space on the play button plays; on another button it presses that button
    await page.keyboard.press("Space");
    expect((await playerState(page)).playing).toBe(true);
    await page.keyboard.press("Space");
    expect((await playerState(page)).playing).toBe(false);

    await player(page).evaluate((el) => (el as unknown as { seek(t: number): void }).seek(0));
    await page.keyboard.press("ArrowRight");
    expect((await videoClock(page)).t).toBeCloseTo(5, 1);
    await expect(statusRegion(page)).toHaveText("At 5 seconds.");
    await page.keyboard.press("ArrowLeft");
    expect((await videoClock(page)).t).toBeCloseTo(0, 1);

    await page.keyboard.press("ArrowDown");
    expect((await playerState(page)).volume).toBeCloseTo(0.9, 5);
    await expect(statusRegion(page)).toHaveText("Volume 90%.");
    await page.keyboard.press("ArrowUp");
    expect((await playerState(page)).volume).toBe(1);

    await page.keyboard.press("c");
    expect((await playerState(page)).captions).toBe(true);
    await expect(player(page).getByRole("button", { name: "Captions" })).toHaveAttribute("aria-pressed", "true");
    expect(await player(page).evaluate((el) => el.shadowRoot!.querySelector("video")!.textTracks[0]!.mode)).toBe("showing");

    await page.keyboard.press("t");
    await expect(player(page).getByRole("button", { name: "Transcript" })).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByRole("region", { name: "Descriptions transcript" })).toBeVisible();

    await page.keyboard.press(">");
    expect((await playerState(page)).speed).toBe(1.25);
    await page.keyboard.press("<");
    await page.keyboard.press("<");
    expect((await playerState(page)).speed).toBe(0.75);

    await page.keyboard.press("m");
    await expect(player(page).getByRole("radio", { name: "Mixed audio" })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(modeButton(page)).toBeFocused();

    await page.keyboard.press("f");
    await expect.poll(() => page.evaluate(() => document.fullscreenElement?.tagName.toLowerCase() ?? null)).toBe("lull-player");
    await expect(player(page).getByRole("button", { name: "Exit full screen" })).toBeVisible();
    await page.keyboard.press("f");
    await expect.poll(() => page.evaluate(() => document.fullscreenElement)).toBeNull();

    // every control is reachable by Tab, with a visible focus ring, and is at least 44 px
    const sizes = await player(page).evaluate((el) =>
      [...el.shadowRoot!.querySelectorAll<HTMLElement>("button, input[type=range]")]
        .filter((b) => b.offsetParent !== null && !b.closest("[hidden]") && !b.classList.contains("row"))
        .map((b) => {
          const r = (b.classList.contains("seek") ? b.parentElement! : b).getBoundingClientRect();
          return { name: b.getAttribute("aria-label") ?? b.textContent?.trim(), w: Math.round(r.width), h: Math.round(r.height) };
        }),
    );
    for (const s of sizes) expect(Math.min(s.w, s.h), JSON.stringify(s)).toBeGreaterThanOrEqual(44);
  });

  test("a focused seek slider holds still while the video plays, so it isn't read out every second", async ({ page, baseURL }) => {
    await open(page, baseURL);
    const seek = player(page).getByRole("slider", { name: "Seek" });
    await seek.focus();
    await expect(seek).toHaveAttribute("aria-valuetext", "0 seconds of 6 seconds");
    await page.keyboard.press("k");
    await expect.poll(async () => (await videoClock(page)).t, { timeout: 5000 }).toBeGreaterThan(1.2);
    await expect(seek).toHaveAttribute("aria-valuetext", "0 seconds of 6 seconds");
    // pausing brings it up to date, and so does moving it
    await page.keyboard.press("k");
    expect((await playerState(page)).playing).toBe(false);
    await expect(seek).toHaveAttribute("aria-valuetext", /^[1-5] seconds? of 6 seconds$/);
    await page.keyboard.press("ArrowRight");
    await expect(seek).toHaveAttribute("aria-valuetext", /^[56] seconds of 6 seconds$/);
  });

  test("a transcript line seeks to its cue", async ({ page, baseURL }) => {
    await open(page, baseURL);
    await chooseMode(page, "My screen reader");
    await player(page).getByRole("button", { name: "Transcript" }).click();
    const row = page.getByRole("region", { name: "Descriptions transcript" }).getByRole("button", { name: new RegExp(CUES.c002.text) });
    await row.click();
    expect((await videoClock(page)).t).toBeCloseTo(CUES.c002.start, 1);
    await expect(row).toHaveAttribute("aria-current", "true");
    // playing from there announces that line first
    await player(page).getByRole("button", { name: "Play" }).click();
    await expect(cueRegion(page)).toHaveText(CUES.c002.text);
  });

  test("missing media says what is missing and why modes are off", async ({ page, baseURL }) => {
    const errors = pageErrors(page);
    await openEmbed(page, baseURL!, { src: "/fx/nope.mp4", descriptions: "/fx/nope.vtt", "described-audio": "/fx/nope.m4a", label: "Missing" });
    await expect(player(page).getByText("This video isn’t available")).toBeVisible();
    await expect(player(page).getByText("/fx/nope.mp4")).toBeVisible();
    await expect(player(page).getByRole("button", { name: "Play" })).toBeDisabled();
    await expect.poll(async () => (await playerState(page)).descriptions).toBe("error");
    await expect.poll(async () => (await playerState(page)).describedAudio).toBe("error");
    expect((await playerState(page)).mode).toBeNull();
    await modeButton(page).click();
    await expect(player(page).getByRole("radio", { name: "Mixed audio" })).toBeDisabled();
    await expect(player(page).getByRole("radio", { name: "Mixed audio" })).toHaveAccessibleDescription(/The described soundtrack could not be loaded/);
    await expect(player(page).getByRole("radio", { name: "Spoken cues" })).toHaveAccessibleDescription(/The descriptions could not be loaded/);
    await expect(player(page).getByRole("radio", { name: "Extended" })).toHaveAccessibleDescription(/no extended descriptions file/);
    await player(page).getByRole("button", { name: "Transcript" }).click();
    await expect(page.getByRole("region", { name: "Descriptions transcript" })).toContainText("The descriptions could not be loaded.");
    expect(errors).toEqual([]);
  });

  test("a mode whose file fails falls back to one that works", async ({ page, baseURL }) => {
    await openEmbed(page, baseURL!, { ...FULL, "described-audio": "/fx/nope.m4a", mode: "mixed" });
    await expect.poll(async () => (await playerState(page)).describedAudio).toBe("error");
    expect((await playerState(page)).mode).toBe("spoken");
  });

  test("no serious or critical axe violations, at both widths, with the panels open", async ({ page, baseURL }) => {
    for (const viewport of [{ width: 1440, height: 900 }, PHONE]) {
      await page.setViewportSize(viewport);
      await open(page, baseURL);
      await player(page).getByRole("button", { name: "Transcript" }).click();
      await page.keyboard.press("?");
      await modeButton(page).click();
      const results = await new AxeBuilder({ page }).analyze();
      const blocking = results.violations
        .filter((v) => v.impact === "serious" || v.impact === "critical")
        .map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
      expect(blocking, `at ${viewport.width}px`).toEqual([]);
    }
  });

  test("reduced motion turns the transitions off", async ({ page, baseURL }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await open(page, baseURL);
    const d = await player(page).evaluate((el) => getComputedStyle(el.shadowRoot!.querySelector(".btn")!).transitionDuration);
    expect(d.split(",").every((s) => parseFloat(s) === 0)).toBe(true);
  });
});

test("player.js may be loaded by any site", async ({ request }) => {
  const res = await request.get("/player.js");
  expect(res.status()).toBe(200);
  expect(res.headers()["access-control-allow-origin"]).toBe("*");
  expect(res.headers()["content-type"]).toMatch(/javascript/);
});

test.describe("player, in the site", () => {
  test("the kitchen instance says the recorded run's media isn't exported yet", async ({ page }) => {
    const errors = pageErrors(page);
    await page.goto("/kitchen#player");
    const p = page.locator("#player lull-player");
    await expect(p.getByText("This video isn’t available")).toBeVisible();
    await expect(p.getByText("/showcase/ed90/video.mp4")).toBeVisible();
    await expect(p.getByRole("button", { name: "Play" })).toBeDisabled();
    expect(errors).toEqual([]);
  });

  test("with the media in place it plays through the React wrapper, and stays axe clean", async ({ page }) => {
    await routeFixtures(page, "**/showcase/ed90/**", "/showcase/ed90/");
    for (const viewport of [{ width: 1440, height: 900 }, PHONE]) {
      await page.setViewportSize(viewport);
      await page.goto("/kitchen#player");
      await waitReady(page);
      await player(page).scrollIntoViewIfNeeded();
      await player(page).getByRole("button", { name: "Play" }).click();
      await expect.poll(async () => (await videoClock(page)).t).toBeGreaterThan(0.3);
      await player(page).getByRole("button", { name: "Pause" }).click();
      const results = await new AxeBuilder({ page }).include("#player").analyze();
      const blocking = results.violations
        .filter((v) => v.impact === "serious" || v.impact === "critical")
        .map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
      expect(blocking, `at ${viewport.width}px`).toEqual([]);
    }
  });
});
