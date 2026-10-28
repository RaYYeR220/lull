import path from "node:path";
import type { NextConfig } from "next";
import { PHASE_PRODUCTION_BUILD } from "next/constants";
import { assertApiBaseForBuild } from "./lib/api-base";
import { assertRealShowcases } from "./lib/showcase-build";

/** Sent with every response. */
const SECURITY_HEADERS = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

/**
 * The embeddable player is loaded by other sites as a module script, which browsers fetch
 * with CORS: it must say any origin may use it.
 */
const PLAYER_HEADERS = [
  { key: "Access-Control-Allow-Origin", value: "*" },
  { key: "Cross-Origin-Resource-Policy", value: "cross-origin" },
  { key: "Cache-Control", value: "public, max-age=300, stale-while-revalidate=86400" },
];

const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // web/ is its own pnpm project; keep file tracing from walking up to other lockfiles
  outputFileTracingRoot: __dirname,
  async headers() {
    return [
      { source: "/:path*", headers: SECURITY_HEADERS },
      { source: "/player.js", headers: PLAYER_HEADERS },
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
    assertApiBaseForBuild({ NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL });
    assertRealShowcases(SHOWCASE_ROOT);
  }
  return config;
}
