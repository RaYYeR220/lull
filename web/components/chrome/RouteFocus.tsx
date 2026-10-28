"use client";

import { usePathname } from "next/navigation";
import { useEffect, useRef } from "react";
import { MAIN_ID } from "@/lib/site";

/**
 * After a client-side move to another page, focus goes to the new page's main content (as a
 * full page load would start at the top), so the next Tab doesn't walk the nav again. The first
 * render and moves within a page (a hash, a query) leave focus alone.
 */
export function RouteFocus() {
  const pathname = usePathname();
  const last = useRef(pathname);
  useEffect(() => {
    if (last.current === pathname) return;
    last.current = pathname;
    const main = document.getElementById(MAIN_ID);
    // a page that already placed focus inside its content keeps it
    if (main && !main.contains(document.activeElement)) main.focus({ preventScroll: true });
  }, [pathname]);
  return null;
}
