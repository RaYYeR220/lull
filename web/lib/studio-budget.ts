/**
 * A line's word budget against its pause, the way the engine counts it: spoken words are
 * whitespace tokens that hold a letter or a digit (plan.count_words), and a pause holds
 * floor(usable seconds × the voice's words per second) of them (timeline.capacity_words).
 */

const SPOKEN = /[\p{L}\p{N}]/u;

/** Spoken words: whitespace tokens that hold a letter or digit ("2.1" is one word, "—" none). */
export function countWords(text: string): number {
  let n = 0;
  for (const tok of text.split(/\s+/)) if (tok && SPOKEN.test(tok)) n += 1;
  return n;
}

export type Window = { start: number; end: number; capacity: number };

/**
 * The voice's rate the pauses were counted at, worked back from their capacities. Each pause
 * says capacity ≤ seconds × wps < capacity + 1, so the rate lies in the intersection of those
 * ranges; the middle of it is returned with its bounds. Null when there are no usable pauses
 * or the ranges don't meet (capacities counted some other way).
 */
export function inferWps(gaps: readonly Window[]): { wps: number; low: number; high: number } | null {
  let low = 0;
  let high = Infinity;
  for (const g of gaps) {
    const s = g.end - g.start;
    if (!(s > 0) || !Number.isFinite(g.capacity) || g.capacity < 0) continue;
    low = Math.max(low, g.capacity / s);
    high = Math.min(high, (g.capacity + 1) / s);
  }
  if (!Number.isFinite(high) || !(low > 0) || low > high + 1e-9) return null;
  return { wps: (low + high) / 2, low, high };
}

export type Budget = {
  words: number;
  /** How many words the pause holds; null for a line without a pause (extended). */
  capacity: number | null;
  /** Words to spare (negative: over). */
  left: number | null;
  over: boolean;
  /** The pause's usable length, seconds. */
  room: number | null;
  /** About how long the line takes to say at the voice's rate, seconds; null without a rate. */
  seconds: number | null;
  /** The share of the pause the line fills, 0 upwards (over 1 when it is too long). */
  fill: number | null;
};

/** The line's words against its pause. */
export function budget(text: string, gap: Window | null, wps: number | null): Budget {
  const words = countWords(text);
  const seconds = wps && wps > 0 ? words / wps : null;
  if (!gap) return { words, capacity: null, left: null, over: false, room: null, seconds, fill: null };
  const room = Math.max(0, gap.end - gap.start);
  const fill = gap.capacity > 0 ? words / gap.capacity : words > 0 ? Infinity : 0;
  return { words, capacity: gap.capacity, left: gap.capacity - words, over: words > gap.capacity, room, seconds, fill };
}

/** "14 of 19 words", "3 words over: 22 of 19". */
export function budgetWords(b: Budget): string {
  if (b.capacity === null) return `${b.words} ${b.words === 1 ? "word" : "words"}`;
  if (b.over) {
    const n = b.words - b.capacity;
    return `${n} ${n === 1 ? "word" : "words"} over: ${b.words} of ${b.capacity}`;
  }
  return `${b.words} of ${b.capacity} words`;
}
