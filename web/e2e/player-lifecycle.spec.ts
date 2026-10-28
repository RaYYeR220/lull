import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { CUES, EMBED, FIXTURES, FULL, instrument, openEmbed, playerState, serveFile, videoClock, waitReady, watched, type PlayerAttrs } from "./player-fixtures";

/**
 * The player's recordings, its life in and out of the page, and its limits: the engine's
 * <id>.m4a recordings with a .wav retry, the system voice when there are none (and saying
 * so), closing audio on removal, bounded holds, outside plays during a hold, crossorigin
 * changes, seeks in screen reader mode, lines cut by pause, and clips at 1.25x.
 */
const player = (page: Page) => page.locator("lull-player");
const statusRegion = (page: Page) => player(page).locator('[data-live="status"]');
const cueRegion = (page: Page) => player(page).locator('[data-live="cue"]');
const modeButton = (page: Page) => player(page).getByRole("button", { name: /^Description mode/ });
const seek = (page: Page, t: number) => player(page).evaluate((el, at) => (el as unknown as { seek(t: number): void }).seek(at), t);

type Entry = { what: string; t: number; at: number; detail?: Record<string, unknown> };

async function open(page: Page, baseURL: string | undefined, attrs: PlayerAttrs = FULL) {
  await openEmbed(page, baseURL!, attrs);
  await waitReady(page);
}

async function chooseMode(page: Page, name: string) {
  await modeButton(page).click();
  await player(page).getByRole("radio", { name }).check();
  if ((await modeButton(page).getAttribute("aria-expanded")) === "true") await page.keyboard.press("Escape");
}

/** Records cue events and the video's play/pause with wall-clock times, in the page. */
async function record(page: Page) {
  await player(page).evaluate((el) => {
    const v = el.shadowRoot!.querySelector("video")!;
    const w = window as unknown as { log: Entry[] };
    w.log = [];
    const push = (what: string, detail?: Record<string, unknown>) => w.log.push({ what, t: v.currentTime, at: performance.now(), detail });
    v.addEventListener("pause", () => push("pause"));
    v.addEventListener("play", () => push("play"));
    el.addEventListener("lull-cue", (e) => push("cue", (e as CustomEvent).detail));
  });
}
const log = (page: Page) => page.evaluate(() => (window as unknown as { log?: Entry[] }).log ?? []);
const cueEvents = async (page: Page) => (await log(page)).filter((e) => e.what === "cue").map((e) => e.detail!);

test("recordings are <id>.m4a by default, and a missing .m4a is tried once as .wav", async ({ page, baseURL }) => {
  const asked: string[] = [];
  page.on("request", (r) => {
    if (r.url().includes("/fx/audio/")) asked.push(new URL(r.url()).pathname);
  });
  await instrument(page);
  await open(page, baseURL, { ...FULL, mode: "spoken" });
  await record(page);
  await player(page).getByRole("button", { name: "Play" }).click();
  await expect.poll(() => cueEvents(page), { timeout: 8000 }).toHaveLength(2);
  expect(await cueEvents(page)).toMatchObject([
    { id: "c001", via: "clip" },
    { id: "c002", via: "clip" },
  ]);
  expect(asked).toContain("/fx/audio/c001.m4a");
  expect(asked).toContain("/fx/audio/c002.m4a");
  expect(asked).toContain("/fx/audio/c002.wav");
  expect(asked).not.toContain("/fx/audio/c001.wav");
  expect((await watched(page)).speech).toEqual([]);
  await expect(player(page).locator(".meta")).toContainText("Recorded voice");
});

test("cue-audio-ext names the recordings' extension, with .wav still tried after", async ({ page, baseURL }) => {
  const asked: string[] = [];
  page.on("request", (r) => {
    if (r.url().includes("/fx/audio/")) asked.push(new URL(r.url()).pathname);
  });
  await open(page, baseURL, { ...FULL, "cue-audio-ext": "mp3", mode: "spoken" });
  await expect.poll(() => asked).toContain("/fx/audio/c001.wav");
  expect(asked.indexOf("/fx/audio/c001.mp3")).toBeGreaterThanOrEqual(0);
  expect(asked.indexOf("/fx/audio/c001.mp3")).toBeLessThan(asked.indexOf("/fx/audio/c001.wav"));
  expect(asked.filter((u) => u.endsWith(".m4a"))).toEqual([]);
});

test("with no recordings the system voice speaks, and the player shows and says why", async ({ page, baseURL }) => {
  await instrument(page);
  await open(page, baseURL, { ...FULL, "cue-audio-base": "/fx/none/", mode: "spoken" });
  const why = "Recorded voice unavailable, using system voice";
  await expect(player(page).locator(".meta")).toContainText(why);
  await player(page).getByRole("button", { name: "Play" }).click();
  await expect(statusRegion(page)).toContainText(why);
  await expect.poll(async () => (await watched(page)).speech.map((s) => s.text), { timeout: 10_000 }).toContain(CUES.c001.text);
  await modeButton(page).click();
  await expect(player(page).getByRole("radio", { name: "Spoken cues" })).toHaveAccessibleDescription(new RegExp(why));
});

test("taking the player out of the page closes its audio context; putting it back plays again", async ({ page, baseURL }) => {
  await instrument(page);
  await open(page, baseURL, { ...FULL, mode: "spoken" });
  await player(page).getByRole("button", { name: "Play" }).click();
  await expect.poll(async () => (await playerState(page)).programme, { timeout: 4000 }).toBe("graph");
  await page.evaluate(() => {
    const el = document.querySelector("lull-player")!;
    const w = window as unknown as { parked: Element; oldVideo: Element };
    w.parked = el;
    w.oldVideo = el.shadowRoot!.querySelector("video")!;
    el.remove();
  });
  await expect.poll(async () => (await watched(page)).ctxs).toEqual(["closed"]);
  await page.evaluate(() => document.querySelector("main")!.append((window as unknown as { parked: Element }).parked));
  expect(await playerState(page)).toMatchObject({ programme: "element", playing: false });
  // the routed video can't leave its closed context: the player took a fresh one
  expect(await player(page).evaluate((el) => el.shadowRoot!.querySelector("video") !== (window as unknown as { oldVideo: Element }).oldVideo)).toBe(true);
  await waitReady(page);
  await seek(page, 0.8);
  await record(page);
  await player(page).getByRole("button", { name: "Play" }).click();
  await expect.poll(() => cueEvents(page), { timeout: 4000 }).toMatchObject([{ id: "c001", via: "clip" }]);
  expect((await playerState(page)).programme).toBe("graph");
  expect((await watched(page)).ctxs).toEqual(["closed", "running"]);
});

test("putting the player back in mixed audio brings the described track back", async ({ page, baseURL }) => {
  await open(page, baseURL);
  await player(page).getByRole("button", { name: "Play" }).click();
  await page.waitForTimeout(500);
  await page.evaluate(() => {
    const el = document.querySelector("lull-player")!;
    el.remove();
    document.querySelector("main")!.append(el);
  });
  await player(page).getByRole("button", { name: "Play" }).click();
  await page.waitForTimeout(700);
  const s = await player(page).evaluate((el) => {
    const v = el.shadowRoot!.querySelector("video")!;
    const a = el.shadowRoot!.querySelector("audio")!;
    return { muted: v.muted, audioPaused: a.paused, drift: Math.abs(a.currentTime - v.currentTime), videoPaused: v.paused };
  });
  expect(s).toMatchObject({ muted: true, audioPaused: false, videoPaused: false });
  expect(s.drift).toBeLessThan(0.15);
});

test("taking the player out aborts its loads, and putting it back loads them again", async ({ page, baseURL }) => {
  await open(page, baseURL);
  let asked = 0;
  await page.route(`${EMBED}/fx/slow.vtt`, async (route) => {
    asked++;
    await new Promise((r) => setTimeout(r, 1200));
    await serveFile(route, path.join(FIXTURES, "descriptions.vtt")).catch(() => undefined);
  });
  await player(page).evaluate((el) => el.setAttribute("descriptions", "/fx/slow.vtt"));
  // out of the page while the file is still on its way
  await expect.poll(() => asked).toBe(1);
  await page.evaluate(() => {
    const el = document.querySelector("lull-player")!;
    el.remove();
    (window as unknown as { parked: Element }).parked = el;
  });
  await page.waitForTimeout(1600);
  expect(await page.evaluate(() => (window as unknown as { parked: { state: { descriptions: string } } }).parked.state.descriptions)).not.toBe("ready");
  await page.evaluate(() => document.querySelector("main")!.append((window as unknown as { parked: Element }).parked));
  await expect.poll(async () => (await playerState(page)).descriptions, { timeout: 4000 }).toBe("ready");
  expect(asked).toBe(2);
});

test("an extended hold never runs past 30 s, by NOTE or by span", async ({ page, baseURL }) => {
  await instrument(page);
  await open(page, baseURL, { ...FULL, extended: "/fx/extended-long.vtt", mode: "extended" });
  await player(page).getByRole("button", { name: "Transcript" }).click();
  const rows = page.getByRole("region", { name: "Descriptions transcript" });
  await expect(rows).toContainText("A wait that would stall the video.");
  await expect(rows).not.toContainText("999");
  await record(page);
  await player(page).getByRole("button", { name: "Play" }).click();
  await expect.poll(async () => (await playerState(page)).holding, { timeout: 4000 }).toBe(true);
  await expect(player(page).locator(".hold")).toContainText("The video waits 30.00 s for this line");
  await seek(page, 3.9);
  await expect.poll(async () => (await cueEvents(page)).filter((d) => d.kind === "extended"), { timeout: 4000 }).toHaveLength(2);
  expect((await cueEvents(page)).filter((d) => d.kind === "extended").map((d) => d.hold)).toEqual([30, 30]);
});

test("at 0.75x an extended hold is still bounded at 30 s, and the band says so", async ({ page, baseURL }) => {
  await instrument(page);
  await open(page, baseURL, { ...FULL, extended: "/fx/extended-long.vtt", mode: "extended" });
  await player(page).getByRole("button", { name: "Play" }).focus();
  await page.keyboard.press("<");
  expect((await playerState(page)).speed).toBe(0.75);
  await record(page);
  await player(page).getByRole("button", { name: "Play" }).click();
  await expect.poll(async () => (await playerState(page)).holding, { timeout: 4000 }).toBe(true);
  // 30 s / 0.75 would be 40 s: the wall-clock wait is bounded after the speed is applied
  await expect(player(page).locator(".hold")).toContainText("The video waits 30.00 s for this line");
  await expect(player(page).locator(".hold")).not.toContainText("40.00");
  // the event still reports the hold in media seconds
  await expect.poll(async () => (await cueEvents(page)).filter((d) => d.kind === "extended").map((d) => d.hold)).toEqual([30]);
});

test("a play from outside during a hold is undone", async ({ page, baseURL }) => {
  await open(page, baseURL, { ...FULL, mode: "extended" });
  await seek(page, 2.3);
  await player(page).getByRole("button", { name: "Play" }).click();
  await expect.poll(async () => (await playerState(page)).holding, { timeout: 3000 }).toBe(true);
  // a media key or picture-in-picture plays the element itself
  await player(page).evaluate((el) => void el.shadowRoot!.querySelector("video")!.play());
  await page.waitForTimeout(400);
  expect((await playerState(page)).holding).toBe(true);
  expect((await videoClock(page)).paused).toBe(true);
  expect(Math.abs((await videoClock(page)).t - CUES.x001.start)).toBeLessThan(0.06);
});

test("changing crossorigin reloads the media where it was", async ({ page, baseURL }) => {
  // a fresh load of a file starts from its first byte
  const fresh: string[] = [];
  page.on("request", (r) => {
    if (/\/fx\/.*\.(mp4|m4a)$/.test(r.url()) && r.headers()["range"] === "bytes=0-") fresh.push(new URL(r.url()).pathname);
  });
  await open(page, baseURL);
  await seek(page, 2);
  await page.waitForTimeout(600);
  fresh.length = 0;
  await player(page).evaluate((el) => el.setAttribute("crossorigin", "anonymous"));
  await expect.poll(() => fresh).toContain("/fx/video.mp4");
  await expect.poll(() => fresh).toContain("/fx/described-audio.m4a");
  await waitReady(page);
  await expect
    .poll(() => player(page).evaluate((el) => el.shadowRoot!.querySelector("video")!.currentTime))
    .toBeCloseTo(2, 1);
  expect(await player(page).evaluate((el) => el.shadowRoot!.querySelector("video")!.crossOrigin)).toBe("anonymous");
});

test("my screen reader: a seek into a line announces it", async ({ page, baseURL }) => {
  await open(page, baseURL, { ...FULL, mode: "screen-reader" });
  await seek(page, 1.5);
  await expect(cueRegion(page)).toHaveText(CUES.c001.text);
  await seek(page, 4);
  await expect(cueRegion(page)).toHaveText(CUES.c002.text);
});

test("spoken cues: a line cut short by pause is said again on play", async ({ page, baseURL }) => {
  await instrument(page);
  const attrs: PlayerAttrs = { ...FULL, mode: "spoken" };
  delete attrs["cue-audio-base"];
  await open(page, baseURL, attrs);
  await player(page).getByRole("button", { name: "Play" }).click();
  await expect.poll(async () => (await watched(page)).speech.length, { timeout: 4000 }).toBe(1);
  await page.waitForTimeout(300);
  await player(page).getByRole("button", { name: "Pause" }).click();
  expect((await watched(page)).speech[0]).toMatchObject({ text: CUES.c001.text, cancelled: true });
  await player(page).getByRole("button", { name: "Play" }).click();
  await expect.poll(async () => (await watched(page)).speech.map((s) => s.text)).toEqual([CUES.c001.text, CUES.c001.text]);
});

test("at 1.25x the recorded lines play at 1.25x and an extended hold is shorter to match", async ({ page, baseURL }) => {
  await instrument(page);
  await open(page, baseURL, { ...FULL, mode: "spoken" });
  await player(page).getByRole("button", { name: "Play" }).focus();
  await page.keyboard.press(">");
  expect((await playerState(page)).speed).toBe(1.25);
  await player(page).getByRole("button", { name: "Play" }).click();
  await expect.poll(async () => (await watched(page)).starts, { timeout: 4000 }).toEqual([1.25]);

  await player(page).getByRole("button", { name: "Pause" }).click();
  await chooseMode(page, "Extended");
  await seek(page, 2.3);
  await record(page);
  await player(page).getByRole("button", { name: "Play" }).click();
  await expect.poll(async () => (await playerState(page)).holding, { timeout: 3000 }).toBe(true);
  await expect.poll(async () => (await playerState(page)).holding, { timeout: 3000 }).toBe(false);
  const entries = await log(page);
  const pausedAt = entries.find((e) => e.what === "pause" && Math.abs(e.t - CUES.x001.start) < 0.1);
  const resumed = entries.find((e) => e.what === "play" && !!pausedAt && e.at > pausedAt.at);
  expect(pausedAt, JSON.stringify(entries)).toBeTruthy();
  expect(resumed, JSON.stringify(entries)).toBeTruthy();
  const heldFor = (resumed!.at - pausedAt!.at) / 1000;
  expect(heldFor).toBeGreaterThan(CUES.x001.pause / 1.25 - 0.1);
  expect(heldFor).toBeLessThan(CUES.x001.pause / 1.25 + 0.25);
  expect((await watched(page)).starts.at(-1)).toBe(1.25);
});
