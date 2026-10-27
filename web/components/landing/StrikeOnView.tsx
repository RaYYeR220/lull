"use client";

import { useEffect, useRef, type ReactNode } from "react";

/**
 * The page's one scroll moment: the refusal's strike draws itself the first time the sheet
 * comes into view. The resting state is struck; the sheet is only un-struck (data-pending)
 * while it waits below the fold, and never under reduced motion.
 */
export function StrikeOnView({ className, children }: { className?: string; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el || !("IntersectionObserver" in window)) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    if (el.getBoundingClientRect().top <= window.innerHeight) return;
    el.dataset.pending = "";
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          delete el.dataset.pending;
          io.disconnect();
        }
      },
      { threshold: 0.45 },
    );
    io.observe(el);
    return () => {
      io.disconnect();
      delete el.dataset.pending;
    };
  }, []);

  return (
    <div ref={ref} className={className}>
      {children}
    </div>
  );
}
