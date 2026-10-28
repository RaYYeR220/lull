import type { MetadataRoute } from "next";
import { SITE_URL } from "@/lib/site-url";

/** Crawlers may read the site, not a visitor's runs or the design-token page. */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: { userAgent: "*", allow: "/", disallow: ["/run/", "/kitchen"] },
    ...(SITE_URL ? { sitemap: `${SITE_URL}/sitemap.xml` } : {}),
  };
}
