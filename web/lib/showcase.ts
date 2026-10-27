import { readFile } from "node:fs/promises";
import path from "node:path";
import { cache } from "react";

export type CueStatus = "verified" | "approved" | "edited" | "held" | "extended";
export type Verdict = "supported" | "not_visible" | "contradicted";

export type ShowcaseGap = { id: string; start: number; end: number; capacity: number };
export type ShowcaseCue = { id: string; start: number; end: number; text: string; status: CueStatus; ahead: boolean };
export type ShowcaseWord = { text: string; start: number; end: number };

/**
 * One recorded run, as the landing and the showcase pages read it. The engine writes it
 * (`lull export-showcase`) to public/showcase/<id>/showcase.json.
 */
export type Showcase = {
  id: string;
  title: string;
  credit: string;
  /** Seconds. */
  duration: number;
  /** dBFS per 100 ms, floor -120. */
  loudness: number[];
  /** Speech spans, seconds. */
  speech: [number, number][];
  /** Usable windows: the pauses, guards already taken off, and how many words fit. */
  gaps: ShowcaseGap[];
  cues: ShowcaseCue[];
  /** URLs under /showcase/<id>/. When present, the hero plays real sound at 1x. */
  audio?: { original: string; described: string };
  /** One draft line and the verifier's ruling on each of its claims. */
  verifyExample?: { cue: string; claims: { text: string; verdict: Verdict }[] };
  /** Model spend for the whole run, USD. */
  costUsd?: number;
  /** Optional transcript words; when present the guarantee figure quotes them. */
  words?: ShowcaseWord[];
  /** True while the file is the placeholder built from the design data, not an engine export. */
  interim?: boolean;
};

const STATUSES: ReadonlySet<string> = new Set(["verified", "approved", "edited", "held", "extended"]);
const VERDICTS: ReadonlySet<string> = new Set(["supported", "not_visible", "contradicted"]);
const ID = /^[a-z0-9-]+$/;

class ShowcaseError extends Error {
  constructor(id: string, where: string, what: string) {
    super(`showcase ${id}: ${where} ${what}`);
    this.name = "ShowcaseError";
  }
}

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const isStr = (v: unknown): v is string => typeof v === "string";

/** Checks a parsed showcase.json against the schema and returns it typed. Throws on the first problem. */
export function parseShowcase(raw: unknown, id: string): Showcase {
  const fail = (where: string, what: string): never => {
    throw new ShowcaseError(id, where, what);
  };
  if (!isObj(raw)) return fail("file", "is not an object");
  const r = raw;

  for (const k of ["id", "title", "credit"] as const) if (!isStr(r[k]) || !r[k]) fail(k, "must be a non-empty string");
  if (r.id !== id) fail("id", `is ${String(r.id)}, expected ${id}`);
  if (!isNum(r.duration) || r.duration <= 0) fail("duration", "must be a positive number");
  const duration = r.duration as number;

  if (!Array.isArray(r.loudness) || !r.loudness.every(isNum)) fail("loudness", "must be an array of numbers");
  const loudness = (r.loudness as number[]).map((db) => Math.max(-120, db));

  if (!Array.isArray(r.speech)) fail("speech", "must be an array");
  const speech = (r.speech as unknown[]).map((s, i): [number, number] => {
    const [a, b] = Array.isArray(s) && s.length === 2 ? (s as unknown[]) : [];
    if (!isNum(a) || !isNum(b) || a > b) return fail(`speech[${i}]`, "must be [start, end]");
    return [a, b];
  });

  if (!Array.isArray(r.gaps)) fail("gaps", "must be an array");
  const gaps = (r.gaps as unknown[]).map((g, i) => {
    if (!isObj(g) || !isStr(g.id) || !isNum(g.start) || !isNum(g.end) || !isNum(g.capacity) || g.start >= g.end)
      return fail(`gaps[${i}]`, "must be { id, start < end, capacity }");
    return { id: g.id, start: g.start, end: g.end, capacity: g.capacity };
  });

  if (!Array.isArray(r.cues)) fail("cues", "must be an array");
  const cues = (r.cues as unknown[]).map((c, i) => {
    if (!isObj(c) || !isStr(c.id) || !isNum(c.start) || !isNum(c.end) || !isStr(c.text) || c.start > c.end)
      return fail(`cues[${i}]`, "must be { id, start <= end, text }");
    if (!isStr(c.status) || !STATUSES.has(c.status)) fail(`cues[${i}].status`, `is not one of ${[...STATUSES].join(", ")}`);
    if (typeof c.ahead !== "boolean") fail(`cues[${i}].ahead`, "must be a boolean");
    return { id: c.id, start: c.start, end: c.end, text: c.text, status: c.status as CueStatus, ahead: c.ahead as boolean };
  });

  const out: Showcase = { id, title: r.title as string, credit: r.credit as string, duration, loudness, speech, gaps, cues };

  if (r.audio !== undefined) {
    const a = r.audio;
    if (!isObj(a) || !isStr(a.original) || !isStr(a.described)) return fail("audio", "must be { original, described }");
    for (const url of [a.original, a.described]) if (!url.startsWith(`/showcase/${id}/`)) fail("audio", `URL ${url} is not under /showcase/${id}/`);
    out.audio = { original: a.original, described: a.described };
  }
  if (r.verifyExample !== undefined) {
    const v = r.verifyExample;
    if (!isObj(v) || !isStr(v.cue) || !Array.isArray(v.claims)) return fail("verifyExample", "must be { cue, claims }");
    const claims = v.claims.map((c, i) => {
      if (!isObj(c) || !isStr(c.text) || !isStr(c.verdict) || !VERDICTS.has(c.verdict))
        return fail(`verifyExample.claims[${i}]`, "must be { text, verdict }");
      return { text: c.text, verdict: c.verdict as Verdict };
    });
    out.verifyExample = { cue: v.cue, claims };
  }
  if (r.costUsd !== undefined) {
    if (!isNum(r.costUsd) || r.costUsd < 0) fail("costUsd", "must be a non-negative number");
    out.costUsd = r.costUsd as number;
  }
  if (r.words !== undefined) {
    if (!Array.isArray(r.words)) fail("words", "must be an array");
    out.words = (r.words as unknown[]).map((w, i) => {
      if (!isObj(w) || !isStr(w.text) || !isNum(w.start) || !isNum(w.end)) return fail(`words[${i}]`, "must be { text, start, end }");
      return { text: w.text, start: w.start, end: w.end };
    });
  }
  if (r.interim === true) out.interim = true;
  return out;
}

/** Reads public/showcase/<id>/showcase.json at build time. Cached per request. */
export const loadShowcase = cache(async (id: string): Promise<Showcase> => {
  if (!ID.test(id)) throw new ShowcaseError(id, "id", "must be lowercase letters, digits and dashes");
  const file = path.join(process.cwd(), "public", "showcase", id, "showcase.json");
  const raw: unknown = JSON.parse(await readFile(file, "utf8"));
  return parseShowcase(raw, id);
});
