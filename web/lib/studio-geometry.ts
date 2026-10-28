/**
 * The studio timeline's geometry: time to pixels at a zoom, the zoom steps, keeping a moment
 * still while zooming, scrolling a cue into view, ruler ticks, and which cue is next.
 */

/** Pixels per second the zoom steps through; "fit" picks whatever shows the whole clip. */
export const ZOOM_STEPS = [6, 8, 12, 16, 24, 32, 48, 64, 96, 128] as const;
export const MIN_PPS: number = ZOOM_STEPS[0];
export const MAX_PPS: number = ZOOM_STEPS[ZOOM_STEPS.length - 1]!;

export const clampPps = (pps: number) => Math.min(MAX_PPS, Math.max(MIN_PPS, pps));

/** The zoom that shows the whole clip in `width` pixels (never below the closest step's floor). */
export function fitPps(width: number, duration: number): number {
  if (!(duration > 0) || !(width > 0)) return MIN_PPS;
  return Math.max(MIN_PPS / 2, Math.min(MAX_PPS, width / duration));
}

/** The next zoom step in (dir 1) or out (dir -1) from `pps`. */
export function stepPps(pps: number, dir: 1 | -1): number {
  if (dir > 0) return ZOOM_STEPS.find((z) => z > pps + 1e-6) ?? MAX_PPS;
  return [...ZOOM_STEPS].reverse().find((z) => z < pps - 1e-6) ?? Math.min(pps, MIN_PPS);
}

export const timeToX = (t: number, pps: number) => t * pps;
export const xToTime = (x: number, pps: number, duration: number) => Math.min(duration, Math.max(0, x / pps));

/**
 * The scroll position that keeps the moment under `anchor` (pixels from the view's left edge)
 * where it is when the zoom goes from `from` to `to` pixels per second.
 */
export function zoomScroll(scrollLeft: number, anchor: number, from: number, to: number, viewWidth: number, duration: number): number {
  const t = (scrollLeft + anchor) / from;
  const max = Math.max(0, duration * to - viewWidth);
  return Math.min(max, Math.max(0, t * to - anchor));
}

/**
 * The scroll position that shows [x0, x1] with `margin` pixels to spare, moving as little as
 * possible; a span wider than the view is shown from its start.
 */
export function scrollToShow(x0: number, x1: number, scrollLeft: number, viewWidth: number, margin = 48): number {
  const lo = x0 - margin;
  const hi = x1 + margin;
  if (hi - lo >= viewWidth) return Math.max(0, lo);
  if (lo < scrollLeft) return Math.max(0, lo);
  if (hi > scrollLeft + viewWidth) return Math.max(0, hi - viewWidth);
  return scrollLeft;
}

const TICK_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600];

/** Ruler ticks every `step` seconds, labelled at least `minGap` pixels apart. */
export function rulerTicks(duration: number, pps: number, minGap = 72): { step: number; ticks: number[] } {
  const step = TICK_STEPS.find((s) => s * pps >= minGap) ?? TICK_STEPS[TICK_STEPS.length - 1]!;
  const ticks: number[] = [];
  for (let t = 0; t <= duration + 1e-6; t += step) ticks.push(Math.round(t * 1000) / 1000);
  return { step, ticks };
}

/** A block on a lane: where it starts and how wide it is, at least `minWidth` pixels. */
export function block(start: number, end: number | null, pps: number, minWidth = 10): { left: number; width: number } {
  const left = timeToX(start, pps);
  const width = end !== null && end > start ? (end - start) * pps : 0;
  return { left, width: Math.max(minWidth, width) };
}

/** The id `dir` steps from `id` in `ids` (time order), clamped at the ends; the first when `id` isn't there. */
export function neighbour(ids: readonly string[], id: string | null, dir: 1 | -1 | "first" | "last"): string | null {
  if (!ids.length) return null;
  if (dir === "first") return ids[0]!;
  if (dir === "last") return ids[ids.length - 1]!;
  const i = id === null ? -1 : ids.indexOf(id);
  if (i < 0) return ids[0]!;
  return ids[Math.min(ids.length - 1, Math.max(0, i + dir))]!;
}

/** A waveform's strokes: one per loudness frame (dBFS, 100 ms each), louder frames taller, `half` at the loudest. */
export function waveHeights(loudness: readonly number[], half: number): number[] {
  return loudness.map((db) => {
    const a = Math.min(1, Math.max(0, (db + 60) / 44));
    return Math.pow(a, 1.3) * half + 0.4;
  });
}

export type LabelSpot = { id: string; left: number; label: string; /** Drawn centred on `left` (an extended mark's). */ centred?: boolean };

/**
 * The labels that can show without running into each other, in time order: each label is
 * about `charWidth` pixels a character, and shows only when it starts `gap` pixels past the end
 * of the last one shown. The lines keep their full names for assistive tech either way.
 */
export function declutter(spots: readonly LabelSpot[], { charWidth, gap }: { charWidth: number; gap: number }): Set<string> {
  const shown = new Set<string>();
  let end = -Infinity;
  for (const s of [...spots].sort((a, b) => a.left - b.left)) {
    const w = s.label.length * charWidth;
    const start = s.centred ? s.left - w / 2 : s.left;
    if (start < end + gap) continue;
    shown.add(s.id);
    end = start + w;
  }
  return shown;
}
