import { PHASE_DEVELOPMENT_SERVER, PHASE_PRODUCTION_BUILD } from "next/constants";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEV_API_BASE, assertApiBaseForBuild, resolveApiBase } from "../lib/api-base";
import nextConfig from "../next.config";

describe("the API's address", () => {
  it("is NEXT_PUBLIC_API_URL when set, without a trailing slash", () => {
    expect(resolveApiBase({ NEXT_PUBLIC_API_URL: "https://api.lull.example/", NODE_ENV: "production" })).toBe("https://api.lull.example");
    expect(resolveApiBase({ NEXT_PUBLIC_API_URL: " https://api.lull.example ", NODE_ENV: "development" })).toBe("https://api.lull.example");
  });

  it("falls back to a local API only in development and tests", () => {
    expect(DEV_API_BASE).toBe("http://localhost:8000");
    expect(resolveApiBase({ NODE_ENV: "development" })).toBe(DEV_API_BASE);
    expect(resolveApiBase({ NODE_ENV: "test" })).toBe(DEV_API_BASE);
    expect(resolveApiBase({ NEXT_PUBLIC_API_URL: "  ", NODE_ENV: "test" })).toBe(DEV_API_BASE);
    // a production page never quietly calls someone's localhost
    expect(resolveApiBase({ NODE_ENV: "production" })).toBe("");
  });

  it("must be set for a production build", () => {
    expect(() => assertApiBaseForBuild({})).toThrow(/NEXT_PUBLIC_API_URL/);
    expect(() => assertApiBaseForBuild({ NEXT_PUBLIC_API_URL: " " })).toThrow(/NEXT_PUBLIC_API_URL/);
    expect(() => assertApiBaseForBuild({ NEXT_PUBLIC_API_URL: "https://api.lull.example" })).not.toThrow();
  });
});

describe("next.config", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("refuses a production build without the API's address", () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "");
    expect(() => nextConfig(PHASE_PRODUCTION_BUILD)).toThrow(/NEXT_PUBLIC_API_URL/);
  });

  it("builds with it, and runs the dev server without it", () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.lull.example");
    expect(nextConfig(PHASE_PRODUCTION_BUILD).poweredByHeader).toBe(false);
    vi.stubEnv("NEXT_PUBLIC_API_URL", "");
    expect(nextConfig(PHASE_DEVELOPMENT_SERVER).poweredByHeader).toBe(false);
  });
});
