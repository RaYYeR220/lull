import { PHASE_DEVELOPMENT_SERVER, PHASE_PRODUCTION_BUILD } from "next/constants";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEV_API_BASE, assertApiBaseForBuild, contentSecurityPolicy, resolveApiBase } from "../lib/api-base";
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

  it("must be https, except a local API (and the e2e build's)", () => {
    expect(() => assertApiBaseForBuild({ NEXT_PUBLIC_API_URL: "http://api.lull.example" })).toThrow(/https/);
    expect(() => assertApiBaseForBuild({ NEXT_PUBLIC_API_URL: "ftp://api.lull.example" })).toThrow(/https/);
    expect(() => assertApiBaseForBuild({ NEXT_PUBLIC_API_URL: "not a url" })).toThrow(/https/);
    expect(() => assertApiBaseForBuild({ NEXT_PUBLIC_API_URL: "http://localhost:8000" })).not.toThrow();
    expect(() => assertApiBaseForBuild({ NEXT_PUBLIC_API_URL: "http://127.0.0.1:8000" })).not.toThrow();
    expect(() => assertApiBaseForBuild({ NEXT_PUBLIC_API_URL: "http://mock.test:3218", LULL_E2E_BUILD: "1" })).not.toThrow();
  });
});

describe("the content security policy", () => {
  it("lets pages reach only this site, the API and blobs, and be framed by no one", () => {
    const csp = contentSecurityPolicy("https://api.lull.example/v0");
    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain("connect-src 'self' https://api.lull.example blob:");
    expect(csp).toContain("media-src 'self' https://api.lull.example blob:");
    expect(csp).toContain("img-src 'self' https://api.lull.example data: blob:");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).not.toContain("unsafe-eval");
  });
});

describe("next.config", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("refuses a production build without the API's address", () => {
    vi.stubEnv("NEXT_PUBLIC_SITE_URL", "https://lull.example");
    vi.stubEnv("NEXT_PUBLIC_API_URL", "");
    expect(() => nextConfig(PHASE_PRODUCTION_BUILD)).toThrow(/NEXT_PUBLIC_API_URL/);
  });

  it("refuses a production build that doesn't know the site's own address", () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.lull.example");
    vi.stubEnv("NEXT_PUBLIC_SITE_URL", "");
    vi.stubEnv("VERCEL_PROJECT_PRODUCTION_URL", "");
    vi.stubEnv("LULL_E2E_BUILD", "");
    expect(() => nextConfig(PHASE_PRODUCTION_BUILD)).toThrow(/NEXT_PUBLIC_SITE_URL/);
  });

  it("a production build sends the policy with every page", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.lull.example");
    vi.stubEnv("NEXT_PUBLIC_SITE_URL", "https://lull.example");
    const rules = await nextConfig(PHASE_PRODUCTION_BUILD).headers!();
    const all = rules.find((r) => r.source === "/:path*")!;
    expect(all.headers.find((h) => h.key === "Content-Security-Policy")?.value).toContain("connect-src 'self' https://api.lull.example blob:");
  });

  it("builds with it, and runs the dev server without it", () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.lull.example");
    vi.stubEnv("NEXT_PUBLIC_SITE_URL", "https://lull.example");
    expect(nextConfig(PHASE_PRODUCTION_BUILD).poweredByHeader).toBe(false);
    vi.stubEnv("NEXT_PUBLIC_API_URL", "");
    expect(nextConfig(PHASE_DEVELOPMENT_SERVER).poweredByHeader).toBe(false);
  });
});
