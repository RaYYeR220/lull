import { describe, expect, it } from "vitest";
import { siteUrl } from "../lib/site-url";

describe("this site's public address", () => {
  it("is the one named, else Vercel's production domain, else unknown", () => {
    expect(siteUrl({ NEXT_PUBLIC_SITE_URL: "https://lull.example/" })).toBe("https://lull.example");
    expect(siteUrl({ VERCEL_PROJECT_PRODUCTION_URL: "lull.vercel.app" })).toBe("https://lull.vercel.app");
    expect(siteUrl({ NEXT_PUBLIC_SITE_URL: "https://lull.example", VERCEL_PROJECT_PRODUCTION_URL: "x.vercel.app" })).toBe("https://lull.example");
    expect(siteUrl({})).toBeNull();
  });
});
