/**
 * The showcase.json schema, shared by the build (lib/showcase.ts) and the sync script
 * (scripts/sync-showcase.mjs). Plain JavaScript so Node runs it without a build step; the
 * types are in showcase-schema.d.mts.
 *
 * The file is the engine's export (`lull export-showcase`): {id, title, credit, duration,
 * loudness, speech, gaps, cues, words [{t, s, e}], audio {original, described}, cueAudioBase,
 * verifyExample {cueId, cue, claims}, costUsd}. Every path in it is relative to the showcase
 * folder; parseShowcase refuses any other path and returns them as site URLs under
 * /showcase/<id>/.
 */

export const STATUSES = Object.freeze(["verified", "approved", "edited", "held", "extended"]);
export const VERDICTS = Object.freeze(["supported", "not_visible", "contradicted"]);

/** A showcase id: also its folder name and the last part of /watch/<id>. */
export const SHOWCASE_ID = /^[a-z0-9][a-z0-9-]{0,63}$/;
/** Cue and pause ids: they name files (audio/<id>.m4a, keyframes/<id>.jpg). */
export const CUE_ID = /^[A-Za-z0-9_-]{1,64}$/;
/** A relative file path: plain segments, none starting with a dot, no drive, scheme or backslash. */
const REL_FILE = /^(?:[A-Za-z0-9_-][A-Za-z0-9._-]*\/)*[A-Za-z0-9_-][A-Za-z0-9._-]*$/;
/** A relative folder path, ending in a slash. */
const REL_DIR = /^(?:[A-Za-z0-9_-][A-Za-z0-9._-]*\/)+$/;
/** A path that names a place outside the folder: rooted, a drive, a share, or a URL. */
const ABSOLUTE = /^(?:[\\/]|[A-Za-z][A-Za-z0-9+.-]*:)/;

/** Why `v` can't be a path inside the showcase folder, or null when it can. */
function pathProblem(v, dir) {
  if (!isStr(v) || !v) return "must be a path inside the showcase folder";
  if (ABSOLUTE.test(v)) return `is an absolute path (${JSON.stringify(v)}); paths must be relative to the showcase folder`;
  if (!(dir ? REL_DIR : REL_FILE).test(v))
    return `must be a plain path inside the showcase folder${dir ? ", ending in /" : ""} (no "..", dot files or odd characters), not ${JSON.stringify(v)}`;
  return null;
}

/** The export's fixed file names, beside showcase.json. */
export const LAYOUT = Object.freeze({
  json: "showcase.json",
  video: "video.mp4",
  descriptions: "descriptions.vtt",
  extended: "extended.vtt",
  keyframes: "keyframes/",
  cueAudioExt: "m4a",
});

export class ShowcaseError extends Error {
  /**
   * @param {string} id
   * @param {string} where
   * @param {string} what
   */
  constructor(id, where, what) {
    super(`showcase ${id}: ${where} ${what}`);
    this.name = "ShowcaseError";
  }
}

const isObj = (v) => typeof v === "object" && v !== null && !Array.isArray(v);
const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const isStr = (v) => typeof v === "string";

/** The site URL of a path inside the showcase folder. */
export const showcaseUrl = (id, rel = "") => `/showcase/${id}/${rel}`;

/**
 * Checks a parsed showcase.json against the schema and returns it in the site's form (words
 * as {text, start, end}, paths as URLs). Throws a ShowcaseError naming the first bad field.
 *
 * @param {unknown} raw the parsed file
 * @param {string} id the folder it was read from
 */
export function parseShowcase(raw, id) {
  const fail = (where, what) => {
    throw new ShowcaseError(id, where, what);
  };
  if (!SHOWCASE_ID.test(id)) fail("id", "must be lowercase letters, digits and dashes");
  if (!isObj(raw)) fail("file", "is not an object");
  const r = raw;

  for (const k of ["id", "title", "credit"]) if (!isStr(r[k]) || !r[k].trim()) fail(k, "must be a non-empty string");
  if (r.id !== id) fail("id", `is ${String(r.id)}, expected ${id}`);
  if (!isNum(r.duration) || r.duration <= 0) fail("duration", "must be a positive number");

  if (!Array.isArray(r.loudness) || !r.loudness.length || !r.loudness.every(isNum)) fail("loudness", "must be a non-empty array of numbers");
  const loudness = r.loudness.map((db) => Math.max(-120, db));

  if (!Array.isArray(r.speech)) fail("speech", "must be an array");
  const speech = r.speech.map((s, i) => {
    const [a, b] = Array.isArray(s) && s.length === 2 ? s : [];
    if (!isNum(a) || !isNum(b) || a > b) fail(`speech[${i}]`, "must be [start, end]");
    return [a, b];
  });

  if (!Array.isArray(r.gaps)) fail("gaps", "must be an array");
  const gaps = r.gaps.map((g, i) => {
    if (!isObj(g) || !isStr(g.id) || !CUE_ID.test(g.id) || !isNum(g.start) || !isNum(g.end) || !isNum(g.capacity) || g.start >= g.end || g.capacity < 0)
      fail(`gaps[${i}]`, "must be { id, start < end, capacity >= 0 }");
    return { id: g.id, start: g.start, end: g.end, capacity: g.capacity };
  });

  if (!Array.isArray(r.cues)) fail("cues", "must be an array");
  const seen = new Set();
  const cues = r.cues.map((c, i) => {
    if (!isObj(c) || !isStr(c.id) || !isNum(c.start) || !isNum(c.end) || !isStr(c.text) || c.start > c.end)
      fail(`cues[${i}]`, "must be { id, start <= end, text }");
    if (!CUE_ID.test(c.id)) fail(`cues[${i}].id`, "must be letters, digits, _ or -");
    if (seen.has(c.id)) fail(`cues[${i}].id`, `${c.id} appears twice`);
    seen.add(c.id);
    if (!STATUSES.includes(c.status)) fail(`cues[${i}].status`, `is not one of ${STATUSES.join(", ")}`);
    if (typeof c.ahead !== "boolean") fail(`cues[${i}].ahead`, "must be a boolean");
    if (c.extended !== undefined && typeof c.extended !== "boolean") fail(`cues[${i}].extended`, "must be a boolean");
    return { id: c.id, start: c.start, end: c.end, text: c.text, status: c.status, ahead: c.ahead, ...(c.extended !== undefined ? { extended: c.extended } : {}) };
  });

  const out = { id, title: r.title, credit: r.credit, duration: r.duration, loudness, speech, gaps, cues };

  if (r.words !== undefined) {
    if (!Array.isArray(r.words)) fail("words", "must be an array");
    out.words = r.words.map((w, i) => {
      if (!isObj(w) || !isStr(w.t) || !isNum(w.s) || !isNum(w.e) || w.s > w.e) fail(`words[${i}]`, "must be { t, s <= e }");
      return { text: w.t, start: w.s, end: w.e };
    });
  }
  if (r.audio !== undefined) {
    const a = r.audio;
    if (!isObj(a)) fail("audio", "must be { original, described }");
    for (const k of ["original", "described"]) {
      const problem = pathProblem(a[k], false);
      if (problem) fail(`audio.${k}`, problem);
    }
    out.audio = { original: showcaseUrl(id, a.original), described: showcaseUrl(id, a.described) };
  }
  if (r.cueAudioBase !== undefined) {
    const problem = pathProblem(r.cueAudioBase, true);
    if (problem) fail("cueAudioBase", problem);
    out.cueAudioBase = showcaseUrl(id, r.cueAudioBase);
  }
  if (r.verifyExample !== undefined) {
    const v = r.verifyExample;
    if (!isObj(v) || !isStr(v.cue) || !v.cue.trim() || !Array.isArray(v.claims)) fail("verifyExample", "must be { cue, claims }");
    if (v.cueId !== undefined && !(isStr(v.cueId) && seen.has(v.cueId))) fail("verifyExample.cueId", `is not a cue in this run: ${JSON.stringify(v.cueId)}`);
    const claims = v.claims.map((c, i) => {
      if (!isObj(c) || !isStr(c.text) || !c.text || !VERDICTS.includes(c.verdict)) fail(`verifyExample.claims[${i}]`, "must be { text, verdict }");
      return { text: c.text, verdict: c.verdict };
    });
    out.verifyExample = { ...(v.cueId !== undefined ? { cueId: v.cueId } : {}), cue: v.cue, claims };
  }
  if (r.costUsd !== undefined) {
    if (!isNum(r.costUsd) || r.costUsd < 0) fail("costUsd", "must be a non-negative number");
    out.costUsd = r.costUsd;
  }
  if (r.summary !== undefined) {
    if (!isObj(r.summary)) fail("summary", "must be an object");
    out.summary = parseSummary(r.summary, fail);
  }
  if (r.interim === true) out.interim = true;
  return out;
}

const num = (v) => (isNum(v) ? v : null);
const strs = (v) => (Array.isArray(v) ? v.filter(isStr) : []);

/**
 * The export's `summary` (engine lull.showcase.run_summary): what the run's report says. A run
 * published before these were recorded gives empty values: they come back null or empty, and
 * the report leaves those rows out. Malformed parts are refused with their field named.
 */
function parseSummary(s, fail) {
  const counts = {};
  if (s.counts !== undefined) {
    if (!isObj(s.counts)) fail("summary.counts", "must be an object");
    for (const [k, v] of Object.entries(s.counts)) if (STATUSES.includes(k) && isNum(v)) counts[k] = v;
  }
  const cov = isObj(s.coverage) ? s.coverage : {};
  const list = (key, read) => {
    if (s[key] === undefined || s[key] === null) return [];
    if (!Array.isArray(s[key])) fail(`summary.${key}`, "must be an array");
    return s[key].map((x, i) => {
      if (!isObj(x)) fail(`summary.${key}[${i}]`, "must be an object");
      return read(x, i);
    });
  };
  const spend = isObj(s.spend) ? s.spend : {};
  return {
    counts,
    coverage: {
      importantEvents: num(cov.importantEvents),
      described: num(cov.described),
      ratio: num(cov.ratio),
      describedWithExtended: num(cov.describedWithExtended),
      ratioWithExtended: num(cov.ratioWithExtended),
      undescribed: strs(cov.undescribed),
    },
    repetitive: list("repetitive", (x) => ({ id: isStr(x.id) ? x.id : "", text: isStr(x.text) ? x.text : null })),
    f113: list("f113", (x, i) => {
      if (!isNum(x.start) || !isNum(x.end)) fail(`summary.f113[${i}]`, "must have start and end");
      return { gapId: isStr(x.gapId) ? x.gapId : "", start: x.start, end: x.end, eventIds: strs(x.eventIds) };
    }),
    overlapSeconds: num(s.overlapSeconds),
    wcag: list("wcag", (x, i) => {
      if (!isStr(x.criterion) || !isStr(x.status)) fail(`summary.wcag[${i}]`, "must have criterion and status");
      return { criterion: x.criterion, level: isStr(x.level) ? x.level : "", name: isStr(x.name) ? x.name : "", status: x.status, blockers: strs(x.blockers) };
    }),
    models: strs(s.models),
    spend: {
      modelUsdThisRun: num(spend.modelUsdThisRun),
      webSearchesThisRun: num(spend.webSearchesThisRun),
      webSearchListUsdThisRun: num(spend.webSearchListUsdThisRun),
      modelUsdAllRunsOfRunId: num(spend.modelUsdAllRunsOfRunId),
    },
  };
}

/** The relative paths a showcase folder must hold for the site to play it, from its raw file. */
export function requiredFiles(raw) {
  const files = new Set([LAYOUT.json, LAYOUT.video, LAYOUT.descriptions, LAYOUT.extended]);
  if (isObj(raw.audio)) for (const k of ["original", "described"]) if (isStr(raw.audio[k])) files.add(raw.audio[k]);
  const base = isStr(raw.cueAudioBase) ? raw.cueAudioBase : null;
  if (base && Array.isArray(raw.cues)) {
    // every voiced line has its own recording; a held line has none
    for (const c of raw.cues) if (isObj(c) && c.status !== "held") files.add(`${base}${c.id}.${LAYOUT.cueAudioExt}`);
  }
  return [...files];
}

/** One row of public/showcase/index.json. */
export function indexEntry(s) {
  return {
    id: s.id,
    title: s.title,
    credit: s.credit,
    duration: s.duration,
    cues: s.cues.length,
    ...(s.costUsd !== undefined ? { costUsd: s.costUsd } : {}),
  };
}

/**
 * Checks public/showcase/index.json: a list of {id, title, credit, duration, cues, costUsd?},
 * ids unique.
 */
export function parseIndex(raw) {
  const fail = (where, what) => {
    throw new Error(`showcase index: ${where} ${what}`);
  };
  if (!Array.isArray(raw)) fail("file", "must be an array");
  const ids = new Set();
  return raw.map((e, i) => {
    if (!isObj(e) || !isStr(e.id) || !SHOWCASE_ID.test(e.id)) fail(`[${i}].id`, "must be a showcase id");
    if (ids.has(e.id)) fail(`[${i}].id`, `${e.id} appears twice`);
    ids.add(e.id);
    if (!isStr(e.title) || !isStr(e.credit) || !isNum(e.duration) || !Number.isInteger(e.cues) || e.cues < 0)
      fail(`[${i}]`, "must be { id, title, credit, duration, cues }");
    if (e.costUsd !== undefined && !(isNum(e.costUsd) && e.costUsd >= 0)) fail(`[${i}].costUsd`, "must be a non-negative number");
    return { id: e.id, title: e.title, credit: e.credit, duration: e.duration, cues: e.cues, ...(e.costUsd !== undefined ? { costUsd: e.costUsd } : {}) };
  });
}
