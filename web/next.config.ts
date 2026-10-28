import path from "node:path";
import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER, PHASE_PRODUCTION_BUILD } from "next/constants";
import { assertApiBaseForBuild, contentSecurityPolicy, resolveApiBase } from "./lib/api-base";
import { benchmarksFile } from "./lib/benchmarks-file";
import { assertSiteUrlForBuild, siteUrl } from "./lib/site-url";
import { assertRealBenchmarks, assertRealShowcases } from "./lib/showcase-build";

/** Sent with every response. */
const SECURITY_HEADERS = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

/**
 * What other sites may load from this one: the embeddable player (a module script, fetched
 * with CORS) and the recorded runs' public media (CC-licensed clips, their tracks and takes),
 * which the player fetches and plays from the embedding page. Reading only; the range headers
 * are exposed so media can seek.
 */
const CROSS_ORIGIN = [
  { key: "Access-Control-Allow-Origin", value: "*" },
  { key: "Access-Control-Allow-Methods", value: "GET, HEAD" },
  { key: "Access-Control-Allow-Headers", value: "Range" },
  { key: "Access-Control-Expose-Headers", value: "Content-Length, Content-Range, Accept-Ranges" },
  { key: "Cross-Origin-Resource-Policy", value: "cross-origin" },
];
const PLAYER_HEADERS = [...CROSS_ORIGIN, { key: "Cache-Control", value: "public, max-age=300, stale-while-revalidate=86400" }];

const SITE = siteUrl(process.env);

const config: NextConfig = {
  reactStrictMode: true,
  // this site's address, for links that leave it (embed snippets, sharing cards)
  env: SITE ? { NEXT_PUBLIC_SITE_URL: SITE } : {},
  poweredByHeader: false,
  // web/ is its own pnpm project; keep file tracing from walking up to other lockfiles
  outputFileTracingRoot: __dirname,
  // every live run's page and report is one static shell; the id is read in the browser
  async rewrites() {
    return [
      { source: "/run/:id/report", destination: "/run/report" },
      { source: "/run/:id", destination: "/run" },
    ];
  },
  async headers() {
    return [
      { source: "/:path*", headers: SECURITY_HEADERS },
      { source: "/player.js", headers: PLAYER_HEADERS },
      { source: "/showcase/:path*", headers: CROSS_ORIGIN },
    ];
  },
};

/** The recorded runs the site ships. */
export const SHOWCASE_ROOT = path.join(__dirname, "public", "showcase");

/**
 * A production build must know the API's address (the dev server falls back to a local one)
 * and must ship real engine exports, never an interim placeholder.
 */
export default function nextConfig(phase: string): NextConfig {
  if (phase === PHASE_PRODUCTION_BUILD) {
    assertApiBaseForBuild({ NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL, LULL_E2E_BUILD: process.env.LULL_E2E_BUILD });
    assertSiteUrlForBuild(process.env);
    assertRealShowcases(SHOWCASE_ROOT);
    assertRealBenchmarks(benchmarksFile(process.env, __dirname), { allowFixture: process.env.LULL_E2E_BUILD === "1" });
  }
  if (phase === PHASE_DEVELOPMENT_SERVER) return config;
  // built pages carry the policy (the dev server's hot reload needs eval and a socket)
  const csp = { key: "Content-Security-Policy", value: contentSecurityPolicy(resolveApiBase({ NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL, NODE_ENV: "production" })) };
  return {
    ...config,
    async headers() {
      const rules = await config.headers!();
      return rules.map((r) => (r.source === "/:path*" ? { ...r, headers: [...r.headers, csp] } : r));
    },
  };
}
