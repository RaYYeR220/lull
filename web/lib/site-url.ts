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
