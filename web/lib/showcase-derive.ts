/**
 * Pure readings of a showcase run, shared by server sections and the client horizon.
 * Nothing here reads files; everything is derived from the data, never assumed.
 */
import type { CueStatus, Showcase, ShowcaseCue, ShowcaseGap, ShowcaseWord, Verdict } from "./showcase-schema.mjs";
import { formatTimecode } from "./timecode";

/** Statuses that are mixed into the described soundtrack. Held and extended cues are not. */
export const VOICED: ReadonlySet<CueStatus> = new Set(["verified", "edited", "approved"]);
/** Statuses a person gave a line (the engine's "reviewed"). */
export const REVIEWED: ReadonlySet<CueStatus> = new Set(["edited", "approved"]);

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

export type NumberedCue = ShowcaseCue & {
  kind: "standard" | "extended";
  /** 1-based, in time order within its kind. */
  n: number;
  /** "Cue 3" or "Extended 1": how every page names the line. */
  label: string;
  gap: ShowcaseGap | undefined;
};

/**
 * Every cue in time order with the name the pages give it. Standard and extended lines are
 * counted apart, as the engine keeps them (descriptions.vtt and extended.vtt) and as the
 * live run names them.
 */
export function numberedCues(s: Pick<Showcase, "cues" | "gaps">): NumberedCue[] {
  const count = { standard: 0, extended: 0 };
  return [...s.cues]
    .sort((a, b) => a.start - b.start || a.id.localeCompare(b.id))
    .map((c) => {
      const kind = c.status === "extended" ? "extended" : "standard";
      const n = ++count[kind];
      return { ...c, kind, n, label: kind === "extended" ? `Extended ${n}` : `Cue ${n}`, gap: kind === "standard" ? gapOf(c, s.gaps) : undefined };
    });
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

/** Voiced lines that end inside their own pause. */
export function linesInsidePauses(s: Pick<Showcase, "cues" | "gaps">): { inside: number; of: number } {
  const voiced = voicedCues(s);
  return { inside: voiced.filter((c) => c.end <= c.gap.end + 1e-3).length, of: voiced.length };
}

export type CloseUp = {
  gap: ShowcaseGap;
  cue: PlacedCue;
  /** The close-up runs from a to b, seconds. */
  a: number;
  b: number;
  /** Speech spans inside [a, b], clipped. */
  spans: { start: number; end: number }[];
  /** Transcript words inside [a, b], clipped (empty when the run has none). */
  words: { start: number; end: number }[];
  /** The words of the speech just before the pause, and just after it, that show in the close-up. */
  before: ShowcaseWord[];
  after: ShowcaseWord[];
  prevSpeechEnd: number;
  nextSpeechStart: number;
  /** Whether the pause starts (ends) exactly one guard after (before) the speech: only then is a guard drawn. */
  guardBefore: boolean;
  guardAfter: boolean;
  /** Where the illustrative long version would end: past the window, into the next speech. */
  longEnd: number;
};

/**
 * The close-up for the guarantee figure: a voiced line's pause with speech close on both
 * sides, where a line that ran long would hit a word. In order of preference: a pause one
 * guard from speech on each side with transcribed words on both, then one guard from speech
 * on each side, then any pause with speech on both sides; the tightest pause wins within a
 * tier. With no pause bounded by speech, the pause of the longest voiced line.
 */
export function closeUp(s: Showcase): CloseUp | null {
  const voiced = voicedCues(s);
  if (!voiced.length) return null;
  const speech = [...s.speech].sort((x, y) => x[0] - y[0]);
  const words = s.words ?? [];
  const prevSpan = (t: number) => speech.filter(([, e]) => e <= t + 1e-6).at(-1);
  const nextSpan = (t: number) => speech.find(([st]) => st >= t - 1e-6);
  const wordsIn = (span: [number, number] | undefined) =>
    span ? words.filter((w) => w.start >= span[0] - 0.05 && w.end <= span[1] + 0.05) : [];
  const near = (x: number, y: number) => Math.abs(x - y) < 0.05;

  const read = voiced.map((c) => {
    const prev = prevSpan(c.gap.start);
    const next = nextSpan(c.gap.end);
    const guardBefore = !!prev && near(c.gap.start - prev[1], GUARD_S);
    const guardAfter = !!next && near(next[0] - c.gap.end, GUARD_S);
    const tier =
      guardBefore && guardAfter && wordsIn(prev).length && wordsIn(next).length ? 3 : guardBefore && guardAfter ? 2 : prev && next ? 1 : 0;
    return { c, prev, next, guardBefore, guardAfter, tier };
  });
  const best = Math.max(...read.map((r) => r.tier));
  const pool = read.filter((r) => r.tier === best);
  const pick =
    best > 0
      ? pool.reduce((m, r) => (r.c.gap.end - r.c.gap.start < m.c.gap.end - m.c.gap.start ? r : m))
      : pool.reduce((m, r) => (r.c.end - r.c.start > m.c.end - m.c.start ? r : m));

  const { c: cue, prev, next } = pick;
  const { gap } = cue;
  const len = gap.end - gap.start;
  const pad = Math.max(1.5, 0.2 * len);
  const a = Math.max(0, Math.round((gap.start - pad) * 10) / 10);
  const b = Math.min(s.duration, Math.round((gap.end + pad) * 10) / 10);
  const inRange = (x: { start: number; end: number }) => x.end > a && x.start < b;
  const clip = (x: { start: number; end: number }) => ({ start: Math.max(x.start, a), end: Math.min(x.end, b) });
  const ns = next ? next[0] : Infinity;

  return {
    gap,
    cue,
    a,
    b,
    spans: speech.map(([st, e]) => ({ start: st, end: e })).filter(inRange).map(clip),
    words: words.filter(inRange).map(clip),
    // quoted only when the whole word shows in the close-up
    before: wordsIn(prev).filter((w) => w.start >= a),
    after: wordsIn(next).filter((w) => w.end <= b),
    prevSpeechEnd: prev ? prev[1] : -Infinity,
    nextSpeechStart: ns,
    guardBefore: pick.guardBefore,
    guardAfter: pick.guardAfter,
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
 * The cue the verify example became: the one the export names, or failing that the one that
 * says every supported claim. Its text is what was finally spoken.
 */
export function verifiedCue(s: Showcase): NumberedCue | null {
  const v = s.verifyExample;
  if (!v) return null;
  const all = numberedCues(s);
  if (v.cueId !== undefined) return all.find((c) => c.id === v.cueId) ?? null;
  const kept = v.claims.filter((c) => c.verdict === "supported").map((c) => norm(c.text));
  if (!kept.length) return null;
  return all.find((c) => c.kind === "standard" && kept.every((k) => norm(c.text).includes(k))) ?? null;
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

/** The run's model spend, as a figure and in words. */
export function spend(usd: number): { figure: string; words: string } {
  const cents = Math.round(usd * 100);
  if (cents < 1) return { figure: "<$0.01", words: "Under a cent" };
  return { figure: `$${usd.toFixed(2)}`, words: `About ${numberWord(cents)} ${cents === 1 ? "cent" : "cents"}` };
}

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
