/**
 * Pure readings of a showcase run, shared by server sections and the client horizon.
 * Nothing here reads files; everything is derived from the data, never assumed.
 */
import type { CueStatus, Showcase, ShowcaseCue, ShowcaseGap, ShowcaseWord, Verdict } from "./showcase";
import { formatTimecode } from "./timecode";

/** Statuses that are mixed into the described soundtrack. Held and extended cues are not. */
export const VOICED: ReadonlySet<CueStatus> = new Set(["verified", "edited", "approved"]);

/** Clear air the solver leaves on each side of every word, seconds. */
export const GUARD_S = 0.2;

export const wordCount = (text: string): number => text.trim().split(/\s+/).filter(Boolean).length;

export const clamp = (v: number, a: number, b: number) => Math.min(b, Math.max(a, v));

/** The pause a cue was placed in: the gap that contains its start. */
export function gapOf(cue: Pick<ShowcaseCue, "start">, gaps: readonly ShowcaseGap[]): ShowcaseGap | undefined {
  return gaps.find((g) => cue.start >= g.start - 1e-6 && cue.start < g.end);
}

export type PlacedCue = ShowcaseCue & { gap: ShowcaseGap };

/** Cues in time order, each with its pause. Cues that sit in no pause are left out. */
export function placedCues(s: Pick<Showcase, "cues" | "gaps">): PlacedCue[] {
  return s.cues
    .map((c) => ({ ...c, gap: gapOf(c, s.gaps) }))
    .filter((c): c is PlacedCue => c.gap !== undefined)
    .sort((a, b) => a.start - b.start);
}

/** The cues you hear in the described soundtrack. */
export function voicedCues(s: Pick<Showcase, "cues" | "gaps">): PlacedCue[] {
  return placedCues(s).filter((c) => VOICED.has(c.status));
}

/** How much the voiced cues overlap speech: cues that touch a word, and total seconds. */
export function speechOverlap(s: Pick<Showcase, "cues" | "gaps" | "speech">): { cues: number; seconds: number } {
  let cues = 0;
  let seconds = 0;
  for (const c of voicedCues(s)) {
    let mine = 0;
    for (const [a, b] of s.speech) mine += Math.max(0, Math.min(b, c.end) - Math.max(a, c.start));
    if (mine > 0) cues += 1;
    seconds += mine;
  }
  return { cues, seconds };
}

export type CloseUp = {
  gap: ShowcaseGap;
  cue: PlacedCue;
  /** The close-up runs from a to b, seconds. */
  a: number;
  b: number;
  /** Speech inside [a, b]: words when the run has them, otherwise speech spans. */
  marks: { start: number; end: number }[];
  /** Words that end the speech before the window, and start the speech after it. */
  before: ShowcaseWord[];
  after: ShowcaseWord[];
  prevSpeechEnd: number;
  nextSpeechStart: number;
  /** Where the illustrative long version would end: past the window, into the next speech. */
  longEnd: number;
};

/**
 * The close-up for the guarantee figure: the tightest pause that holds a voiced line with
 * speech on both sides, where a line that runs long would hit a word. Falls back to the
 * pause with the longest voiced line when no pause is bounded by speech.
 */
export function closeUp(s: Showcase): CloseUp | null {
  const voiced = voicedCues(s);
  if (!voiced.length) return null;
  const prevEnd = (t: number) => Math.max(-Infinity, ...s.speech.filter(([, e]) => e <= t + 1e-6).map(([, e]) => e));
  const nextStart = (t: number) => Math.min(Infinity, ...s.speech.filter(([st]) => st >= t - 1e-6).map(([st]) => st));

  const bounded = voiced.filter((c) => Number.isFinite(prevEnd(c.gap.start)) && Number.isFinite(nextStart(c.gap.end)));
  const pick = bounded.length
    ? bounded.reduce((m, c) => (c.gap.end - c.gap.start < m.gap.end - m.gap.start ? c : m))
    : voiced.reduce((m, c) => (c.end - c.start > m.end - m.start ? c : m));

  const { gap } = pick;
  const len = gap.end - gap.start;
  const pad = Math.max(1.5, 0.2 * len);
  const a = Math.max(0, Math.round((gap.start - pad) * 10) / 10);
  const b = Math.min(s.duration, Math.round((gap.end + pad) * 10) / 10);
  const pe = prevEnd(gap.start);
  const ns = nextStart(gap.end);

  const inRange = (x: { start: number; end: number }) => x.end > a && x.start < b;
  const words = (s.words ?? []).filter(inRange);
  const marks = words.length
    ? words.map((w) => ({ start: Math.max(w.start, a), end: Math.min(w.end, b) }))
    : s.speech.map(([st, e]) => ({ start: st, end: e })).filter(inRange).map((x) => ({ start: Math.max(x.start, a), end: Math.min(x.end, b) }));

  return {
    gap,
    cue: pick,
    a,
    b,
    marks,
    before: words.filter((w) => w.end <= gap.start + 0.01),
    after: words.filter((w) => w.start >= gap.end - 0.01),
    prevSpeechEnd: pe,
    nextSpeechStart: ns,
    longEnd: Number.isFinite(ns) ? Math.min(b - 0.3, ns + 0.9) : Math.min(b - 0.3, gap.end + 1),
  };
}

export type DraftSegment = { text: string; verdict?: Verdict };

/** The draft cut into plain runs and claims, in reading order. Claims not found in the draft are skipped. */
export function draftSegments(v: NonNullable<Showcase["verifyExample"]>): DraftSegment[] {
  const out: DraftSegment[] = [];
  const lower = v.cue.toLowerCase();
  let at = 0;
  for (const c of v.claims) {
    const i = lower.indexOf(c.text.toLowerCase(), at);
    if (i < 0) continue;
    if (i > at) out.push({ text: v.cue.slice(at, i) });
    out.push({ text: v.cue.slice(i, i + c.text.length), verdict: c.verdict });
    at = i + c.text.length;
  }
  if (at < v.cue.length) out.push({ text: v.cue.slice(at) });
  return out;
}

/** The draft with every unsupported claim cut, as the verifier leaves it. */
export function spokenFromDraft(v: NonNullable<Showcase["verifyExample"]>): string {
  let line = "";
  for (const seg of draftSegments(v)) {
    if (seg.verdict && seg.verdict !== "supported") line = line.replace(/[,;:]?\s*$/, "");
    else line += seg.text;
  }
  line = line.replace(/\s+([.,;:!?])/g, "$1").replace(/\s{2,}/g, " ").trim();
  return line.charAt(0).toUpperCase() + line.slice(1);
}

const norm = (t: string) => t.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

/**
 * The cue the verify example belongs to: the one that says every supported claim.
 * Its text is what was finally spoken.
 */
export function verifiedCue(s: Showcase): { cue: PlacedCue; index: number } | null {
  const v = s.verifyExample;
  if (!v) return null;
  const kept = v.claims.filter((c) => c.verdict === "supported").map((c) => norm(c.text));
  if (!kept.length) return null;
  const all = placedCues(s);
  const index = all.findIndex((c) => kept.every((k) => norm(c.text).includes(k)));
  const cue = index >= 0 ? all[index] : undefined;
  return cue ? { cue, index } : null;
}

const SMALL = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"];
/** "four" for 4; digits past twelve. */
export const numberWord = (n: number): string => SMALL[n] ?? String(n);

export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** "elephants-dream" for "Elephants Dream". */
export const slug = (title: string) =>
  title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");

/** A spoken-style time: "9 seconds" under a minute, "1:11" past it. */
function sayTime(t: number): string {
  return t < 60 ? String(Math.round(t)) : formatTimecode(Math.round(t), 0);
}

/** A sentence-ish account of where the speech is, for the horizon's text alternative. */
export function describeSpeech(speech: readonly [number, number][]): string {
  const merged: [number, number][] = [];
  for (const [a, b] of [...speech].sort((x, y) => x[0] - y[0])) {
    const last = merged[merged.length - 1];
    if (last && a - last[1] < 1) last[1] = Math.max(last[1], b);
    else merged.push([a, b]);
  }
  const long = merged.filter(([a, b]) => b - a >= 2);
  const brief = merged.filter(([a, b]) => b - a < 2);
  const parts = long.map(([a, b]) => {
    const unit = b < 60 ? " seconds" : "";
    return `from ${sayTime(a)} to ${sayTime(b)}${unit}`;
  });
  if (brief.length) parts.push(`briefly at ${brief.map(([a]) => (a < 60 ? `${Math.round(a)} seconds` : formatTimecode(Math.round(a), 0))).join(" and ")}`);
  if (parts.length <= 1) return parts.join("");
  return `${parts.slice(0, -1).join(", ")}, and ${parts[parts.length - 1]}`;
}
