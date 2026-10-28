import type { Page } from "@playwright/test";

/**
 * The address the site was built to call (NEXT_PUBLIC_API_URL; a local API by default) and
 * the scripted stand-in that playwright.config starts (e2e/mock-api.mjs).
 */
export const API = (process.env.E2E_API_BASE ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");
export const MOCK = `http://127.0.0.1:${process.env.MOCK_API_PORT ?? 3218}`;
export const RUN_ID = "live-20261027-214500-a1b2c3";
export const RUN_TOKEN = "tok-e2e-0123456789";
/** The mock's copy of the recorded run ed90, and the edit token the fork hands out. */
export const FORK_ID = "live-20261028-091500-f0c4e2";
export const FORK_TOKEN = "tok-fork-e2e-9876543210";
/** A live run the mock lists as still being described, and one that stopped. */
export const RUNNING_ID = "live-20261028-101500-0b5e11";
export const FAILED_ID = "live-20261028-100000-de4d00";

/**
 * A scenario of its own for one test: the mock keeps each scenario's edits apart, so tests that
 * change a run never meet. "<behaviour>--<suffix>" behaves as <behaviour> (see e2e/mock-runs.mjs).
 */
export const own = (behaviour = "studio") => `${behaviour}--${Math.random().toString(36).slice(2, 10)}`;

/** Gives this browser the edit token of a run before the page loads, as a fork or a run start would. */
export async function holdToken(page: Page, runId: string, token: string): Promise<void> {
  await page.addInitScript(([id, t]) => window.localStorage.setItem(`lull-edit-token:${id}`, t), [runId, token] as const);
}

/** Sends the page's API calls to the mock, under a scenario (see the header of mock-api.mjs). */
export async function routeApi(page: Page, scenario: string): Promise<void> {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route(`${API}/**`, (route) => route.continue({ url: route.request().url().replace(API, `${MOCK}/${scenario}`) }));
}

/** Nothing answers at the API's address. */
export async function apiDown(page: Page): Promise<void> {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route(`${API}/**`, (route) => route.abort("connectionrefused"));
}

/** Records every text the polite live region is given. */
export async function recordAnnouncements(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const said: string[] = [];
    (window as unknown as { __said: string[] }).__said = said;
    const watch = () => {
      const region = document.querySelector("main [role=status]");
      if (!region) return false;
      new MutationObserver(() => {
        const t = region.textContent ?? "";
        if (t) said.push(t);
      }).observe(region, { childList: true, characterData: true, subtree: true });
      return true;
    };
    document.addEventListener("DOMContentLoaded", () => {
      if (!watch()) new MutationObserver((_, obs) => watch() && obs.disconnect()).observe(document.body, { childList: true, subtree: true });
    });
  });
}

export const announcements = (page: Page) => page.evaluate(() => (window as unknown as { __said: string[] }).__said ?? []);
