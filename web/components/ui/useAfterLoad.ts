"use client";

import { useSyncExternalStore } from "react";

let loaded = false;
const listeners = new Set<() => void>();

function start() {
  if (typeof window === "undefined" || loaded) return;
  const done = () => {
    const w = window as Window & { requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number };
    const go = () => {
      loaded = true;
      for (const l of listeners) l();
    };
    if (w.requestIdleCallback) w.requestIdleCallback(go, { timeout: 2000 });
    else window.setTimeout(go, 100);
  };
  if (document.readyState === "complete") done();
  else window.addEventListener("load", done, { once: true });
}

/**
 * True once the page has loaded and the browser has had an idle moment: for pictures below the
 * player that would otherwise share the first seconds' bandwidth with what the visitor sees first.
 * False on the server and in the first render, so the page hydrates as it was built.
 */
export function useAfterLoad(): boolean {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      start();
      return () => listeners.delete(cb);
    },
    () => loaded,
    () => false,
  );
}
