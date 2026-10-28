import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { waitReady } from "./player-fixtures";
import { API } from "./try-api";

/**
 * The developer page: a quick start in three tabs (REST with curl, the CLI, the player embed
 * with a live player), the endpoint reference from lib/api-reference.ts, the models, and no
 * MCP section while lib/features.ts says it isn't built.
 */
const PHONE = { width: 390, height: 844 };
const tab = (page: Page, name: string) => page.getByRole("tab", { name });
const snippet = (page: Page, label: string) => page.locator("[data-copy]").filter({ hasText: label }).first();

test.describe("the developer page", () => {
  test("starts with the live API's address and the REST quick start, in order", async ({ page }) => {
    await page.goto("/developers");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Call Lull from a terminal, or play its files on your page.");
    await expect(page.getByText(`Live API ${API}`)).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Developers" })).toHaveAttribute("aria-current", "page");
    const rest = page.getByRole("tabpanel", { name: "REST" });
    await expect(rest.getByRole("heading", { level: 3 })).toHaveText([
      "Point your shell at the API",
      "Check it can take a run",
      "See what it can describe",
      "Describe a sample, and watch it happen",
      "Fetch what it published",
    ]);
    await expect(rest.locator("pre").first()).toHaveText(`export LULL_API="${API}"`);
    await expect(rest.getByText('curl -N "$LULL_API/v1/runs" -F sample=ed45 -F mode=standard -F voice=Sofia')).toBeVisible();
    // the answer's shape, from the API's own fields
    await rest.getByText("What comes back").nth(2).click();
    await expect(rest.getByText(/"edit_token"/).first()).toBeVisible();
  });

  test("a snippet copies to the clipboard", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.goto("/developers");
    const health = snippet(page, "Health");
    await health.getByRole("button", { name: "Copy health" }).click();
    await expect(health.getByRole("status")).toHaveText("Copied.");
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe('curl -s "$LULL_API/v1/health"');
  });

  test("without a clipboard, a snippet is selected for copying by hand", async ({ page }) => {
    await page.addInitScript(() => Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true }));
    await page.goto("/developers");
    const health = snippet(page, "Health");
    await health.getByRole("button", { name: "Copy health" }).click();
    await expect(health.getByRole("status")).toHaveText(/Selected: press Ctrl\+C/);
    expect(await page.evaluate(() => window.getSelection()?.toString())).toBe('curl -s "$LULL_API/v1/health"');
  });

  test("the CLI tab lists the options, settings and exit codes from the engine", async ({ page }) => {
    await page.goto("/developers");
    await tab(page, "CLI").click();
    const cli = page.getByRole("tabpanel", { name: "CLI" });
    await expect(cli.getByText("lull describe lecture.mp4 --out described/ --mode standard --profile education")).toBeVisible();
    for (const opt of ["--out DIR", "--mode standard|extended", "--profile education|entertainment", "--voice NAME", "--source-url URL", "--no-research", "--run-id ID"])
      await expect(cli.getByRole("rowheader", { name: opt, exact: true })).toBeVisible();
    for (const v of ["NEBIUS_API_KEY", "LULL_SPEECH_URL", "LULL_CAP_RUN", "LULL_CAP_DAY", "LULL_CAP_TOTAL", "LULL_TAVILY_RUN", "LULL_TAVILY_DAY"])
      await expect(cli.getByRole("rowheader", { name: new RegExp(`^${v} `) })).toBeVisible();
    await expect(cli.getByRole("table", { name: "Exit codes" }).getByRole("row")).toHaveCount(5);
    await expect(cli.getByText(/lull export-showcase runs\//)).toBeVisible();
  });

  test("the embed snippet works pasted as given onto another site: script and media cross origin", async ({ page }) => {
    await page.goto("/developers");
    await tab(page, "Embed").click();
    const origin = new URL(page.url()).origin;
    const pre = page.getByRole("tabpanel", { name: "Embed" }).locator("pre").first();
    await expect(pre).toContainText(`src="${origin}/player.js"`);
    await expect(pre).toContainText('crossorigin="anonymous"');
    // this site under a public-looking name (Chrome won't let a public page reach localhost),
    // answering with its own responses and headers, CORS ones included
    const site = "http://site.test";
    const snippet = ((await pre.textContent()) ?? "").replaceAll(origin, site);
    await page.route(`${site}/**`, async (route) => {
      const url = new URL(route.request().url());
      const range = route.request().headers()["range"];
      const res = await page.request.get(`${origin}${url.pathname}`, { headers: range ? { Range: range } : {} });
      await route.fulfill({ status: res.status(), headers: res.headers(), body: await res.body() });
    });
    // another site: its own page with the snippet pasted in, everything else from this site
    const other = "http://embed.test";
    await page.route(`${other}/`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "text/html; charset=utf-8",
        body: `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Another site</title></head><body><main>${snippet}</main></body></html>`,
      }),
    );
    const failed: string[] = [];
    page.on("requestfailed", (r) => failed.push(`${r.url()} ${r.failure()?.errorText}`));
    await page.goto(`${other}/`);
    await page.waitForFunction(() => !!customElements.get("lull-player"));
    const state = () => page.locator("lull-player").evaluate((el) => (el as unknown as { state: Record<string, unknown> }).state);
    await expect.poll(async () => (await state()).descriptions, { timeout: 15_000 }).toBe("ready");
    await expect.poll(async () => (await state()).extended, { timeout: 15_000 }).toBe("ready");
    await expect.poll(async () => (await state()).video, { timeout: 15_000 }).toBe("ready");
    // the transcript fills from the fetched track
    await page.locator("lull-player").getByRole("button", { name: "Transcript" }).click();
    await expect(page.locator("lull-player").getByRole("region", { name: "Descriptions transcript" })).toContainText("A second person with dark hair");
    expect(failed.filter((f) => !/ERR_ABORTED/.test(f))).toEqual([]);
    // the headers that make it work
    for (const p of ["/player.js", "/showcase/ed90/descriptions.vtt", "/showcase/ed90/audio/c001.m4a"]) {
      const res = await page.request.get(`${origin}${p}`, { headers: { Origin: other } });
      expect(res.headers()["access-control-allow-origin"], p).toBe("*");
    }
  });

  test("the embed tab documents every attribute, and its live player plays", async ({ page }) => {
    await page.goto("/developers");
    await tab(page, "Embed").click();
    const embed = page.getByRole("tabpanel", { name: "Embed" });
    const origin = new URL(page.url()).origin;
    await expect(embed.locator("pre").first()).toContainText(`<script type="module" src="${origin}/player.js"></script>`);
    await expect(embed.locator("pre").first()).toContainText(`src="${origin}/showcase/ed90/video.mp4"`);
    for (const a of ["src", "descriptions", "described-audio", "extended", "cue-audio-base", "cue-audio-ext", "captions", "poster", "mode", "label", "crossorigin", "sound"])
      await expect(embed.locator(`[data-attribute="${a}"]`)).toHaveCount(1);
    await expect(embed.getByRole("table", { name: /^Keys/ }).getByRole("row")).toHaveCount(10);

    const p = embed.locator("lull-player");
    await expect(p).toHaveAttribute("src", "/showcase/ed90/video.mp4");
    await waitReady(page);
    await p.getByRole("button", { name: "Play" }).click();
    await expect.poll(() => p.evaluate((el) => (el as unknown as { currentTime: number }).currentTime), { timeout: 15_000 }).toBeGreaterThan(0.3);
    await p.getByRole("button", { name: "Pause" }).click();
  });

  test("lists every endpoint with its answer, its refusals and a curl call", async ({ page }) => {
    await page.goto("/developers");
    const endpoints = page.locator("[data-endpoint]");
    await expect(endpoints).toHaveCount(13);
    await expect(page.getByRole("heading", { level: 3 })).toContainText(["Live runs", "Reading runs", "Studio"]);
    const start = page.locator('[data-endpoint="startRun"]');
    await expect(start.getByRole("heading", { level: 4 })).toHaveText("POST/v1/runs");
    const refusals = start.getByRole("table", { name: "Refusals" });
    await expect(refusals.getByRole("rowheader", { name: "rate_limited", exact: true })).toBeVisible();
    await expect(refusals).toContainText("3 runs an hour from you, 20 a day in all");
    const edit = page.locator('[data-endpoint="editCue"]');
    await expect(edit).toContainText("Authorization: Bearer <the run’s edit token>");
    await expect(edit.getByRole("table", { name: "Refusals" })).toContainText("lint");
    for (const name of ["health", "run", "publish", "fork"]) await expect(page.locator(`[data-endpoint="${name}"] pre`).last()).toContainText("$LULL_API/v1/");
  });

  test("names the models and what each does; says nothing of MCP while it isn't built", async ({ page }) => {
    await page.goto("/developers");
    const models = page.getByRole("region", { name: "Models" });
    for (const m of ["Parakeet TDT 0.6B v3", "Nemotron 3.5 Super VL", "Nemotron 3 Ultra", "Nemotron 3.5 Lightning", "Magpie TTS Multilingual 357M"])
      await expect(models.getByRole("rowheader", { name: new RegExp(m) }).first()).toBeVisible();
    await expect(models).toContainText("nvidia/Nemotron-3-Ultra-550b-a55b");
    await expect(page.locator("#mcp")).toHaveCount(0);
    await expect(page.getByRole("navigation", { name: "On this page" }).getByRole("link")).toHaveText(["Quick start", "Endpoints", "Models"]);
    await expect(page.getByText(/describe_video|review_queue|get_tracks/)).toHaveCount(0);
    // the landing makes no MCP claim either
    await page.goto("/");
    await expect(page.getByText(/describe_video|review_queue|get_tracks/)).toHaveCount(0);
    await expect(page.locator("#developers").getByRole("link", { name: "Read the developer docs" })).toHaveAttribute("href", "/developers");
  });

  test("has no serious or critical axe violations and no sideways scroll, at 1440 and 390", async ({ page }) => {
    // four whole-page axe passes over a long reference page: slow when the suite runs in parallel
    test.setTimeout(180_000);
    for (const viewport of [{ width: 1440, height: 900 }, PHONE]) {
      await page.setViewportSize(viewport);
      await page.emulateMedia({ reducedMotion: "reduce" });
      await page.goto("/developers");
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      for (const t of ["REST", "Embed"]) {
        await tab(page, t).click();
        const results = await new AxeBuilder({ page }).analyze();
        const blocking = results.violations
          .filter((v) => v.impact === "serious" || v.impact === "critical")
          .map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
        expect(blocking, `${t} at ${viewport.width}px`).toEqual([]);
      }
      const { scrollWidth, clientWidth } = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth }));
      expect(scrollWidth, `at ${viewport.width}px`).toBeLessThanOrEqual(clientWidth);
    }
  });
});
