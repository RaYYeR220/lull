export const REPO_URL = "https://github.com/RaYYeR220/lull";

/** The skip link and every page's <main> share this id. */
export const MAIN_ID = "main";

/** The recorded run the landing page is drawn from: public/showcase/<id>/showcase.json. */
export const SHOWCASE_ID = "ed90";

export const SITE = {
  name: "Lull",
  claim: "Audio description that waits for the silence.",
  description:
    "Lull watches your video, writes what a blind viewer would miss, fits each line into a real pause, and checks it against the frames before it speaks.",
} as const;

export type SiteLink = {
  href: string;
  label: string;
  /**
   * false until the route ships. Links to it skip prefetching, so the page
   * never requests a route that isn't there yet. Flip it with the route.
   */
  live: boolean;
};

export const NAV_LINKS: readonly SiteLink[] = [
  { href: "/studio", label: "Studio", live: false },
  { href: "/library", label: "Library", live: false },
  { href: "/developers", label: "Developers", live: false },
  { href: "/benchmarks", label: "Benchmarks", live: false },
];

export const FOOTER_LINKS: readonly SiteLink[] = [
  { href: REPO_URL, label: "GitHub", live: true },
  { href: "/developers", label: "Docs", live: false },
  { href: "/benchmarks", label: "Benchmarks", live: false },
];

/**
 * Route prefixes no nav link names that aren't built yet: links into them (a run's
 * /watch/<id> page) skip prefetching too. Remove a prefix when its route ships.
 */
const UNBUILT_PREFIXES: readonly string[] = ["/watch"];

/** Prefetch setting for a link to `href`: default for live routes, off for routes not built yet (and pages under them). */
export function prefetchFor(href: string): false | undefined {
  const under = (base: string) => href === base || href.startsWith(`${base}/`);
  const link = [...NAV_LINKS, ...FOOTER_LINKS].find((l) => l.href.startsWith("/") && under(l.href));
  if (link) return link.live ? undefined : false;
  return UNBUILT_PREFIXES.some(under) ? false : undefined;
}
