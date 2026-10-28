export const REPO_URL = "https://github.com/RaYYeR220/lull";

/** The skip link and every page's <main> share this id. */
export const MAIN_ID = "main";

/** The recorded run the landing page is drawn from: public/showcase/<id>/showcase.json. */
export const SHOWCASE_ID = "ed90";

export const SITE = {
  name: "Lull",
  claim: "Audio description that waits for the silence.",
  description:
    "Lull watches your video, writes lines about what is on screen, fits each into a real pause, and voices only the lines that pass a check against the frames.",
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
  { href: "/try", label: "Try it", live: true },
  { href: "/studio", label: "Studio", live: true },
  // the recorded runs
  { href: "/watch", label: "Library", live: true },
  { href: "/developers", label: "Developers", live: true },
  { href: "/benchmarks", label: "Benchmarks", live: true },
];

export const FOOTER_LINKS: readonly SiteLink[] = [
  { href: REPO_URL, label: "GitHub", live: true },
  { href: "/developers", label: "Docs", live: true },
  { href: "/benchmarks", label: "Benchmarks", live: true },
];

/**
 * Prefetch setting for a link to `href`: off. The pages are static and open quickly on a click;
 * prefetching every route in view spent the first seconds' bandwidth on a phone, ahead of the
 * page being looked at. (Routes not built yet were never prefetched either.)
 */
export function prefetchFor(href: string): false {
  void href;
  return false;
}
