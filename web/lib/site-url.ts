/**
 * This site's public address, when the build knows it: NEXT_PUBLIC_SITE_URL, or on Vercel the
 * production domain (next.config passes it through). Used for absolute links that leave the
 * site (embed snippets, sharing cards); a page in the browser uses its own origin instead.
 */
export function siteUrl(env: Record<string, string | undefined>): string | null {
  const named = env.NEXT_PUBLIC_SITE_URL?.trim();
  if (named) return named.replace(/\/+$/, "");
  const vercel = env.VERCEL_PROJECT_PRODUCTION_URL?.trim();
  return vercel ? `https://${vercel.replace(/\/+$/, "")}` : null;
}

/** The address as the build knew it (inlined), or null. */
export const SITE_URL: string | null = process.env.NEXT_PUBLIC_SITE_URL || null;

/**
 * Stops a production build that doesn't know the site's own address: sharing cards, the
 * sitemap and embed snippets need it absolute, and a deployed page must never point at
 * localhost. The e2e build (LULL_E2E_BUILD=1) runs on localhost and is exempt.
 */
export function assertSiteUrlForBuild(env: Record<string, string | undefined>): void {
  if (env.LULL_E2E_BUILD === "1") return;
  const site = siteUrl(env);
  let ok = false;
  try {
    ok = !!site && /^https?:$/.test(new URL(site).protocol);
  } catch {
    ok = false;
  }
  if (ok) return;
  throw new Error(
    "The site's own address is unknown. A production build needs NEXT_PUBLIC_SITE_URL (e.g. " +
      "NEXT_PUBLIC_SITE_URL=https://lull.example), or, on Vercel, VERCEL_PROJECT_PRODUCTION_URL, " +
      "which Vercel sets itself. For the local build the e2e tests run against, use `pnpm build:e2e`.",
  );
}
