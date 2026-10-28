/**
 * Where the site finds the Lull API. A production build must be told (NEXT_PUBLIC_API_URL,
 * inlined at build time); only development and tests fall back to a local API on uvicorn's
 * default port, so a deployed page never quietly calls a visitor's localhost.
 */
export const DEV_API_BASE = "http://localhost:8000";

type Env = { NEXT_PUBLIC_API_URL?: string; NODE_ENV?: string; LULL_E2E_BUILD?: string };

/** The API's address for this environment, without a trailing slash; "" in production when unset. */
export function resolveApiBase(env: Env): string {
  const set = env.NEXT_PUBLIC_API_URL?.trim();
  if (set) return set.replace(/\/+$/, "");
  return env.NODE_ENV === "production" ? "" : DEV_API_BASE;
}

const LOCAL = new Set(["localhost", "127.0.0.1", "[::1]"]);

/**
 * Stops a production build that doesn't know the API's address, or would call it in the
 * clear: edit tokens travel to it, so it must be https (a local API, or the e2e build's
 * scripted one, excepted).
 */
export function assertApiBaseForBuild(env: Env): void {
  const set = env.NEXT_PUBLIC_API_URL?.trim();
  if (set) {
    let url: URL | null = null;
    try {
      url = new URL(set);
    } catch {
      url = null;
    }
    const ok = url && (url.protocol === "https:" || (url.protocol === "http:" && (LOCAL.has(url.hostname) || env.LULL_E2E_BUILD === "1")));
    if (ok) return;
    throw new Error(`NEXT_PUBLIC_API_URL must be an https address (a local API may be http), not ${JSON.stringify(set)}.`);
  }
  throw new Error(
    "NEXT_PUBLIC_API_URL is not set. A production build needs the Lull API's address, e.g. " +
      "NEXT_PUBLIC_API_URL=https://api.example.org pnpm build. For a local build the e2e tests " +
      "run against, use `pnpm build:e2e` (it points the site at http://localhost:8000).",
  );
}

/**
 * The pages' Content-Security-Policy: scripts, styles and fonts from this site only (Next's
 * inline bootstrap needs 'unsafe-inline'); connections, media and images only to this site,
 * the API and blob: (the player's decoded takes); no plugins; no framing.
 */
export function contentSecurityPolicy(apiBase: string): string {
  let api = "";
  try {
    api = apiBase ? new URL(apiBase).origin : "";
  } catch {
    api = "";
  }
  const and = (x: string) => (api ? `${api} ${x}` : x);
  return [
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline'",
    "font-src 'self'",
    `img-src 'self' ${and("data: blob:")}`,
    `media-src 'self' ${and("blob:")}`,
    `connect-src 'self' ${and("blob:")}`,
    "worker-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
}
