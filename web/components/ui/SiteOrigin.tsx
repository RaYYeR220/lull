"use client";

import { useEffect, useState } from "react";
import { SITE_URL } from "@/lib/site-url";

/** This site's origin, for a snippet pasted elsewhere: the built-in address, then the page's own. */
export function useSiteOrigin(): string {
  const [origin, setOrigin] = useState(SITE_URL ?? "");
  useEffect(() => setOrigin(window.location.origin), []);
  return origin;
}

/** The origin as text, inside a code sample. */
export function SiteOrigin() {
  return <>{useSiteOrigin()}</>;
}
