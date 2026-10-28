import type { MetadataRoute } from "next";
import { loadIndex } from "@/lib/showcase";
import { SITE_URL } from "@/lib/site-url";

/** The public pages and every recorded run with its report; empty when the build doesn't know the site's address. */
export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  if (!SITE_URL) return [];
  const runs = await loadIndex();
  const paths = ["/", "/try", "/watch", "/studio", "/developers", "/benchmarks", ...runs.flatMap((r) => [`/watch/${r.id}`, `/watch/${r.id}/report`, `/studio/${r.id}`])];
  return paths.map((p) => ({ url: `${SITE_URL}${p === "/" ? "" : p}` }));
}
