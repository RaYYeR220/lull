import { describe, expect, it } from "vitest";
import { assertSiteUrlForBuild, siteUrl } from "../lib/site-url";

describe("this site's public address", () => {
  it("is the one named, else Vercel's production domain, else unknown", () => {
    expect(siteUrl({ NEXT_PUBLIC_SITE_URL: "https://lull.example/" })).toBe("https://lull.example");
    expect(siteUrl({ VERCEL_PROJECT_PRODUCTION_URL: "lull.vercel.app" })).toBe("https://lull.vercel.app");
    expect(siteUrl({ NEXT_PUBLIC_SITE_URL: "https://lull.example", VERCEL_PROJECT_PRODUCTION_URL: "x.vercel.app" })).toBe("https://lull.example");
    expect(siteUrl({})).toBeNull();
  });
});

describe("a production build", () => {
  it("must know the site's address: NEXT_PUBLIC_SITE_URL or Vercel's production domain", () => {
    expect(() => assertSiteUrlForBuild({})).toThrow(/NEXT_PUBLIC_SITE_URL.*VERCEL_PROJECT_PRODUCTION_URL/s);
    expect(() => assertSiteUrlForBuild({ NEXT_PUBLIC_SITE_URL: " " })).toThrow(/NEXT_PUBLIC_SITE_URL/);
    expect(() => assertSiteUrlForBuild({ NEXT_PUBLIC_SITE_URL: "lull dot example" })).toThrow(/NEXT_PUBLIC_SITE_URL/);
    expect(() => assertSiteUrlForBuild({ NEXT_PUBLIC_SITE_URL: "https://lull.example" })).not.toThrow();
    expect(() => assertSiteUrlForBuild({ VERCEL_PROJECT_PRODUCTION_URL: "lull.vercel.app" })).not.toThrow();
  });

  it("except the e2e build, which runs on localhost", () => {
    expect(() => assertSiteUrlForBuild({ LULL_E2E_BUILD: "1" })).not.toThrow();
  });
});
