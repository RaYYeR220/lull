/**
 * Where the site finds the Lull API. A production build must be told (NEXT_PUBLIC_API_URL,
 * inlined at build time); only development and tests fall back to a local API on uvicorn's
 * default port, so a deployed page never quietly calls a visitor's localhost.
 */
export const DEV_API_BASE = "http://localhost:8000";

type Env = { NEXT_PUBLIC_API_URL?: string; NODE_ENV?: string };

/** The API's address for this environment, without a trailing slash; "" in production when unset. */
export function resolveApiBase(env: Env): string {
  const set = env.NEXT_PUBLIC_API_URL?.trim();
  if (set) return set.replace(/\/+$/, "");
  return env.NODE_ENV === "production" ? "" : DEV_API_BASE;
}

/** Stops a production build that doesn't know the API's address. */
export function assertApiBaseForBuild(env: Env): void {
  if (env.NEXT_PUBLIC_API_URL?.trim()) return;
  throw new Error(
    "NEXT_PUBLIC_API_URL is not set. A production build needs the Lull API's address, e.g. " +
      "NEXT_PUBLIC_API_URL=https://api.example.org pnpm build. For a local build the e2e tests " +
      "run against, use `pnpm build:e2e` (it points the site at http://localhost:8000).",
  );
}
