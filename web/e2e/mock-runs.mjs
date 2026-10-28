// The run routes of the scripted API (e2e/mock-api.mjs): published runs, their files, the run
// list, and the studio's writes, with the API's owner-token rules (engine/src/lull/api/
// studio.py, store.py, app.py).
//
//   GET  /v1/runs                       the list: the recorded run, the published live runs,
//                                       one still running (RUNNING_ID), one that stopped (FAILED_ID)
//   GET  /v1/runs/<id>                  {manifest, cues {cues, extended}, dirty}; 404 otherwise
//   GET  /v1/runs/<id>/files/<path>     cues.json (with gaps and speech), the VTTs, the mix,
//                                       described.mp4, keyframes, report.html (byte ranges served)
//   GET  /v1/runs/<id>/cues/<cue>/audio the cue's take
//   PATCH /v1/runs/<id>/cues/<cue>      {text}: a tiny lint ("we"/"you" and hedges are errors,
//                                       gendered words an R14 warning), "door" comes back held,
//                                       anything else edited
//   POST .../approve, .../hold {reason?}, /v1/runs/<id>/publish
//
// Runs: ed90 (the recorded run: read-only), FORK_ID (a copy of it, token FORK_TOKEN) and RUN_ID
// (a live run of an uploaded clip, token RUN_TOKEN). Writes need "Authorization: Bearer <token>"
// (403 forbidden otherwise). Every scenario keeps its own copy of what was changed, so tests
// running side by side never see each other's edits; a scenario named "<behaviour>--<suffix>"
// behaves as <behaviour>:
//   overlap   publishing is refused by the overlap gate (409, the first voiced line named)
//   slow      edits take 1.5 s to be checked, a publish 1.5 s
//   empty-list the run list is empty (a run that isn't published is then not there)
//   reread500 once a change is made, reading the run back fails (500)
//   refuse409 every change answers 409 without the API's own body (a proxy, an older API)
//
// Each detail says where the run came from (`source`, from its request.json): FORK_ID is a copy
// of ed90, SAMPLE_ID a run of the sample ed90 whose cues.json predates speech and pauses,
// UPLOAD_ED90_ID an upload that happens to be called ed90.mp4 (the same older cues.json).

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SHOWCASE = path.join(HERE, "..", "public", "showcase", "ed90");

export const RUN_ID = "live-20261027-214500-a1b2c3";
export const RUN_TOKEN = "tok-e2e-0123456789";
export const FORK_ID = "live-20261028-091500-f0c4e2";
export const FORK_TOKEN = "tok-fork-e2e-9876543210";
export const RUNNING_ID = "live-20261028-101500-0b5e11";
export const SAMPLE_ID = "live-20261028-111500-5a3e90";
export const SAMPLE_TOKEN = "tok-sample-e2e-1357924680";
export const UPLOAD_ED90_ID = "live-20261028-112000-0ed90u";
export const FAILED_ID = "live-20261028-100000-de4d00";

const show = JSON.parse(readFileSync(path.join(SHOWCASE, "showcase.json"), "utf8"));

const VL = "nvidia/Nemotron-3.5-Super-VL";
const ULTRA = "nvidia/Nemotron-3-Ultra-550b-a55b";
const LIGHT = "nvidia/Nemotron-3_5-Lightning";
const TTS = "Magpie TTS Multilingual";

/** The claims a line makes, all seen: its clauses. */
function claimsOf(text, start) {
  return text
    .replace(/[.!?]$/, "")
    .split(/,\s*|\s+as\s+|\s+and\s+|\s+while\s+/)
    .map((t) => t.trim())
    .filter((t) => t.split(/\s+/).length >= 2)
    .map((t, i) => ({ text: t.toLowerCase(), verdict: "supported", at: Number((start + 0.5 + i).toFixed(2)) }));
}

const gapOf = (gaps, t) => gaps.find((g) => t >= g.usable_start - 1e-6 && t < g.usable_end);

/** The recorded run as the API's cues.json and detail have it. */
function ed90() {
  const gaps = show.gaps.map((g) => ({ id: g.id, start: g.start, end: g.end, usable_start: g.start, usable_end: g.end, capacity_words: g.capacity, protected: [] }));
  const cue = (c, extended) => {
    const claims =
      show.verifyExample?.cueId === c.id
        ? show.verifyExample.claims.filter((k) => k.verdict === "supported").map((k, i) => ({ ...k, at: Number((c.start + 0.6 + i).toFixed(2)) }))
        : claimsOf(c.text, c.start);
    return {
      id: c.id,
      gap_id: extended ? null : (gapOf(gaps, c.start)?.id ?? null),
      start: c.start,
      end: null,
      text: c.text,
      event_ids: [`e${c.id.slice(1)}`],
      status: c.status,
      ahead: c.ahead,
      claims,
      audio_path: `audio/${c.id}.wav`,
      audio_dur: Number((c.end - c.start).toFixed(3)),
      tempo: 1.0,
      findings: [],
      note:
        show.verifyExample?.cueId === c.id
          ? show.verifyExample.claims
              .filter((k) => k.verdict === "not_visible")
              .map((k) => `removed “${k.text}” (not visible in the frames)`)
              .join("; ") || null
          : null,
    };
  };
  return {
    video: { name: "ed90.mp4", duration: show.duration },
    created: "2026-10-27T18:40:12+00:00",
    voice: "Sofia",
    cost: show.costUsd,
    cues: show.cues.filter((c) => c.status !== "extended").map((c) => cue(c, false)),
    extended: show.cues.filter((c) => c.status === "extended").map((c) => cue(c, true)),
    gaps,
    speech: show.speech.map(([start, end]) => ({ start, end })),
    clip: true,
    source: { kind: "fork", id: "ed90" },
  };
}

/** A run of the recorded clip's cues whose cues.json, from an older engine, has no speech or pauses. */
function older(source, name) {
  return () => ({ ...ed90(), video: { name, duration: show.duration }, gaps: null, speech: null, clip: source.kind === "sample", source });
}

/** The live run of an uploaded clip (the try-it stream's run), with a held line and a reviewer warning. */
function upload() {
  const gaps = [
    { id: "g001", start: 8.12, end: 13.8, usable_start: 8.32, usable_end: 13.6, capacity_words: 16, protected: [] },
    { id: "g002", start: 35.7, end: 41.2, usable_start: 35.9, usable_end: 41.0, capacity_words: 15, protected: [] },
    { id: "g003", start: 50.0, end: 52.2, usable_start: 50.2, usable_end: 52.0, capacity_words: 5, protected: [] },
    { id: "g004", start: 71.04, end: 75.2, usable_start: 71.24, usable_end: 75.0, capacity_words: 11, protected: [] },
    { id: "g005", start: 81.2, end: 84.8, usable_start: 81.4, usable_end: 84.6, capacity_words: 9, protected: [] },
  ];
  const speech = [
    [0.6, 8.12],
    [13.8, 35.7],
    [41.2, 50.0],
    [52.2, 71.04],
    [75.2, 81.2],
    [84.8, 89.5],
  ].map(([start, end]) => ({ start, end }));
  const cue = (id, gap, start, text, status, dur, extra = {}) => ({
    id,
    gap_id: gap,
    start,
    end: null,
    text,
    event_ids: [`e0${id.slice(-1)}`],
    status,
    ahead: false,
    claims: claimsOf(text, start),
    audio_path: dur ? `work/${id}.wav` : null,
    audio_dur: dur,
    tempo: 1.0,
    findings: [],
    note: null,
    ...extra,
  });
  return {
    video: { name: "corridor-walk.mp4", duration: 90 },
    created: "2026-10-27T21:46:38+00:00",
    voice: "Sofia",
    cost: 0.0213,
    cues: [
      cue("c001", "g001", 8.32, "In a dim corridor of cables, an older man, Proog, leads a younger one, Emo.", "verified", 3.1, {
        findings: [{ rule: "R14-gender", message: "gendered word; confirm the programme establishes it", span: [42, 45], severity: "warning" }],
      }),
      cue("c002", "g002", 35.9, "Proog pulls Emo back from the edge as the floor drops away into darkness.", "verified", 4.4, {
        note: "removed “red” (not visible in the frames)",
      }),
      cue("c003", "g004", 71.24, "A metal door swings open onto a bright white room.", "held", null, {
        note: "says a door opens; the frames show no door",
        claims: [
          { text: "a metal door swings open", verdict: "not_visible", at: 72.1 },
          { text: "a bright white room", verdict: "supported", at: 72.9 },
        ],
      }),
      cue("c004", "g005", 81.4, "Emo stares up at a wall of switches, unsure.", "verified", 2.6),
    ],
    extended: [],
    gaps,
    speech,
    clip: false,
    source: { kind: "upload", id: null },
  };
}

const BASE = {
  ed90: () => ({ ...ed90(), source: { kind: "showcase", id: "ed90" } }),
  [FORK_ID]: ed90,
  [RUN_ID]: upload,
  [SAMPLE_ID]: older({ kind: "sample", id: "ed90" }, "ed90.mp4"),
  [UPLOAD_ED90_ID]: older({ kind: "upload", id: null }, "ed90.mp4"),
};
const TOKENS = { [FORK_ID]: FORK_TOKEN, [RUN_ID]: RUN_TOKEN, [SAMPLE_ID]: SAMPLE_TOKEN };

/** scenario|run id -> the run as that scenario has changed it */
const state = new Map();

function runOf(scenario, id) {
  const make = BASE[id];
  if (!make) return null;
  const key = `${scenario}|${id}`;
  if (!state.has(key)) state.set(key, { ...make(), id, dirty: false, version: 0, published: 0 });
  return state.get(key);
}

const VOICED = new Set(["verified", "edited", "approved"]);

function summary(run) {
  const all = [...run.cues, ...run.extended];
  const count = (s) => all.filter((c) => c.status === s).length;
  const held = all.filter((c) => c.status === "held");
  const voiced = all.filter((c) => (VOICED.has(c.status) || c.status === "extended") && c.audio_dur);
  const reviewed = voiced.length > 0 && voiced.every((c) => c.status === "edited" || c.status === "approved");
  const important = 6;
  const described = Math.min(important, run.cues.filter((c) => VOICED.has(c.status)).length);
  const blockers = held.length ? [`${held.length} ${held.length === 1 ? "description" : "descriptions"} held for review`] : [];
  const status = blockers.length ? "needs review" : reviewed ? "met" : "met (automated checks)";
  const warnings = all.flatMap((c) => c.findings.filter((f) => f.severity === "warning").map((f) => ({ cue_id: c.id, rule: f.rule, message: f.message })));
  return {
    cues: { verified: count("verified"), edited: count("edited"), approved: count("approved"), held: held.length, extended: count("extended") },
    coverage: {
      important_events: important,
      salient_events: important,
      described,
      ratio: Number((described / important).toFixed(3)),
      described_with_extended: Math.min(important, described + run.extended.length),
      ratio_with_extended: Number((Math.min(important, described + run.extended.length) / important).toFixed(3)),
      undescribed: [],
      repetitive: ["e09", "e11"],
      watch_salient: 9,
    },
    repetitive: [
      { id: "e09", text: "Wire figures sway in the background." },
      { id: "e11", text: "Cables pulse with light along the walls." },
    ],
    overlap_seconds: 0,
    overlap: { speech_s: 0, between_cues_s: 0, outside_pauses_s: 0, total_s: 0 },
    held: held.map((c) => ({ id: c.id, reason: c.note })),
    f113: [],
    lint_warnings: warnings,
    cost_usd: run.cost,
    cost_per_min: Number((run.cost / 1.5).toFixed(6)),
    calls_by_role_model: [
      { role: "watch", model: VL, calls: 3, tokens_in: 45520, tokens_cached: 0, tokens_out: 1981, cost_usd: 0.0154, latency_s: 16.5 },
      { role: "research", model: LIGHT, calls: 1, tokens_in: 2210, tokens_cached: 0, tokens_out: 180, cost_usd: 0.0002, latency_s: 1.4 },
      { role: "plan", model: ULTRA, calls: 1, tokens_in: 9840, tokens_cached: 0, tokens_out: 1630, cost_usd: 0.0147, latency_s: 18.5 },
      { role: "verify", model: VL, calls: all.length, tokens_in: 25970, tokens_cached: 0, tokens_out: 483, cost_usd: 0.0082, latency_s: 12.3 },
    ],
    wcag: [
      { criterion: "1.2.3", level: "A", name: "Audio Description or Media Alternative (Prerecorded)", status, via: "descriptions.vtt, script.md", reasons: blockers },
      { criterion: "1.2.5", level: "AA", name: "Audio Description (Prerecorded)", status, via: "described.mp4 and described-audio.m4a", reasons: blockers },
      run.extended.length
        ? { criterion: "1.2.7", level: "AAA", name: "Extended Audio Description (Prerecorded)", status, via: "extended.mp4 and extended.vtt", reasons: blockers }
        : { criterion: "1.2.7", level: "AAA", name: "Extended Audio Description (Prerecorded)", status: "not claimed", via: "", reasons: ["extended version not produced"] },
    ],
    reviewed,
    extended_produced: run.extended.length > 0,
    extended_error: null,
    tts_disclosure: `Descriptions are voiced with synthetic speech (${TTS}); ${reviewed ? "every line was reviewed before publishing." : "lines are awaiting review."}`,
    research: { skipped: false, terms: 3, errors: 0 },
  };
}

/**
 * The recorded run's summary as its export carries it (showcase.json `summary`), in the
 * manifest's own shape: the API serves the run's real summary, so the stand-in doesn't invent
 * one for it. What the export doesn't carry (calls, lint warnings) stays the stand-in's.
 */
function exportedSummary(run) {
  const x = show.summary;
  if (!x || run.source?.kind !== "showcase") return null;
  const c = x.counts;
  const reviewed = c.verified === 0 && c.edited + c.approved > 0;
  return {
    cues: c,
    coverage: {
      important_events: x.coverage.importantEvents,
      described: x.coverage.described,
      ratio: x.coverage.ratio,
      described_with_extended: x.coverage.describedWithExtended,
      ratio_with_extended: x.coverage.ratioWithExtended,
      undescribed: x.coverage.undescribed,
    },
    repetitive: x.repetitive,
    overlap_seconds: x.overlapSeconds,
    overlap: null,
    held: null,
    f113: x.f113.map((f) => ({ gap_id: f.gapId, start: f.start, end: f.end, seconds: Number((f.end - f.start).toFixed(3)), event_ids: f.eventIds })),
    cost_usd: x.spend.modelUsdThisRun,
    wcag: x.wcag.map((w) => ({ criterion: w.criterion, level: w.level, name: w.name, status: w.status, via: "", reasons: w.blockers })),
    reviewed,
    tts_disclosure: `Descriptions are voiced with synthetic speech (${TTS}); ${reviewed ? "every line was reviewed before publishing." : "lines are awaiting review."}`,
    spend: { models_usd: x.spend.modelUsdThisRun, tavily_credits: x.spend.webSearchesThisRun },
  };
}

const OUTPUTS = (run) => ({
  "cues.json": { sha256: "0".repeat(64), bytes: 9214 },
  "descriptions.vtt": { sha256: "1".repeat(64), bytes: 1182 },
  "extended.vtt": { sha256: "2".repeat(64), bytes: 402 },
  "described-audio.m4a": { sha256: "3".repeat(64), bytes: 2214406 },
  "described.mp4": { sha256: "4".repeat(64), bytes: 6120931 },
  ...(run.extended.length ? { "extended.mp4": { sha256: "5".repeat(64), bytes: 6390117 } } : {}),
  "script.md": { sha256: "6".repeat(64), bytes: 3120 },
  "report.html": { sha256: "7".repeat(64), bytes: 14022 },
  "manifest.json": { sha256: "8".repeat(64), bytes: 4410 },
});

const tag = (run, c) => `${run.version}${c.audio_dur ?? ""}`.replace(/\D/g, "").slice(0, 10) || "0";

/** A cue as visitors get it: its take as a URL, never a path. */
function view(run, c) {
  const { audio_path, ...rest } = c;
  return { ...rest, audio_url: audio_path ? `/v1/runs/${run.id}/cues/${c.id}/audio?v=${tag(run, c)}` : null };
}

function detail(run) {
  return {
    manifest: {
      run_id: run.id,
      created: run.created,
      video: { name: run.video.name, duration: run.video.duration, sha256: "a".repeat(64) },
      options: { mode: run.extended.length ? "extended" : "standard", profile: "entertainment", voice: run.voice, language: "en" },
      outputs: OUTPUTS(run),
      summary: { ...summary(run), spend: { models_usd: run.cost, tavily_credits: 1 }, ...exportedSummary(run) },
      versions: { engine: "0.4.0", models: [ULTRA, VL, LIGHT], tts: TTS },
    },
    cues: { run_id: run.id, language: "en", cues: run.cues.map((c) => view(run, c)), extended: run.extended.map((c) => view(run, c)) },
    dirty: run.dirty,
    source: run.source,
  };
}

function cuesFile(run) {
  const timeline = run.gaps ? { gaps: run.gaps, speech: run.speech } : {};
  return { run_id: run.id, language: "en", cues: run.cues, extended: run.extended, ...timeline };
}

const vttTime = (t) => {
  const ms = Math.round(t * 1000);
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  const s = ((ms % 60000) / 1000).toFixed(3).padStart(6, "0");
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${s}`;
};

function vtt(cues, extended) {
  const lines = ["WEBVTT", "Kind: descriptions", "Language: en", ""];
  for (const c of cues.filter((c) => (extended || VOICED.has(c.status)) && c.audio_dur)) {
    if (extended) lines.push(`NOTE extended pause=${c.audio_dur.toFixed(3)}`, "");
    lines.push(c.id, `${vttTime(c.start)} --> ${vttTime(c.start + c.audio_dur)}`, c.text, "");
  }
  return lines.join("\n");
}

// -- the API's words ------------------------------------------------------------------------------

const ERR = {
  read_only: [403, "read_only", "Recorded runs are read-only. Fork one to edit a copy of your own."],
  forbidden: [403, "forbidden", "Only the person who started this run can change it. Fork it to edit a copy."],
  no_cue: [404, "not_found", "There is no cue with that id in this run."],
  no_run: [404, "not_found", "There is no run with that id."],
  overlap: [409, "overlap", "Nothing was published: these descriptions would overlap speech, the edge of their pause or each other."],
};

const LINT = [
  { re: /\b(we|you|our|your)\b/i, rule: "R7-person", message: (m) => `first/second person '${m}'; describe in third person`, severity: "error" },
  { re: /\b(appears? to|seems? to|looks? like)\b/i, rule: "R12-hedge", message: (m) => `hedge '${m}'; state what is visible`, severity: "error" },
  { re: /\b(he|she|his|her|him|man|men|woman|women|boy|girl)\b/i, rule: "R14-gender", message: () => "gendered word; confirm the programme establishes it", severity: "warning" },
];

function lint(text) {
  const out = [];
  for (const r of LINT) {
    const m = r.re.exec(text);
    if (m) out.push({ rule: r.rule, message: r.message(m[0]), span: [m.index, m.index + m[0].length], severity: r.severity });
  }
  return out;
}

const autofix = (t) => {
  const s = t.replace(/\s+/g, " ").trim();
  const c = s.charAt(0).toUpperCase() + s.slice(1);
  return /[.!?]$/.test(c) ? c : `${c}.`;
};

const words = (t) => t.split(/\s+/).filter((w) => /[\p{L}\p{N}]/u.test(w)).length;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function body(req) {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const raw = Buffer.concat(chunks).toString("utf8").trim();
  if (!raw) return {};
  try {
    const v = JSON.parse(raw);
    return v && typeof v === "object" && !Array.isArray(v) ? v : null;
  } catch {
    return null;
  }
}

// -- files ----------------------------------------------------------------------------------------

const TYPES = { ".vtt": "text/vtt", ".m4a": "audio/mp4", ".mp4": "video/mp4", ".jpg": "image/jpeg", ".json": "application/json", ".html": "text/html; charset=utf-8", ".md": "text/markdown; charset=utf-8" };

function sendBytes(req, res, cors, buf, type) {
  const range = /^bytes=(\d*)-(\d*)$/.exec(req.headers.range ?? "");
  const head = { "Content-Type": type, "Accept-Ranges": "bytes", ...cors, "Access-Control-Expose-Headers": "Content-Range, Content-Length, Accept-Ranges, Retry-After" };
  if (range) {
    const start = range[1] ? Number(range[1]) : 0;
    const end = range[2] ? Math.min(Number(range[2]), buf.length - 1) : buf.length - 1;
    res.writeHead(206, { ...head, "Content-Range": `bytes ${start}-${end}/${buf.length}`, "Content-Length": end - start + 1 });
    res.end(req.method === "HEAD" ? undefined : buf.subarray(start, end + 1));
    return true;
  }
  res.writeHead(200, { ...head, "Content-Length": buf.length });
  res.end(req.method === "HEAD" ? undefined : buf);
  return true;
}

const fromShowcase = (rel) => {
  try {
    return readFileSync(path.join(SHOWCASE, ...rel.split("/")));
  } catch {
    return null;
  }
};

function serveFile(req, res, cors, run, rel) {
  const ext = path.extname(rel);
  const type = TYPES[ext] ?? "application/octet-stream";
  let buf = null;
  if (rel === "cues.json") buf = Buffer.from(JSON.stringify(cuesFile(run)));
  else if (rel === "descriptions.vtt") buf = run.clip ? fromShowcase("descriptions.vtt") : Buffer.from(vtt(run.cues, false));
  else if (rel === "extended.vtt") buf = run.clip ? fromShowcase("extended.vtt") : Buffer.from(vtt(run.extended, true));
  else if (rel === "described-audio.m4a") buf = fromShowcase("described-audio.m4a");
  else if (rel === "described.mp4") buf = fromShowcase("video.mp4");
  else if (rel === "report.html") buf = Buffer.from(`<!doctype html><title>Report</title><h1>${run.video.name}</h1>`);
  else if (rel === "script.md") buf = Buffer.from(`# ${run.video.name}\n`);
  else if (/^keyframes\/[\w-]+\.jpg$/.test(rel)) buf = fromShowcase(rel);
  if (!buf) {
    res.writeHead(404, { "Content-Type": "application/json", ...cors });
    res.end(JSON.stringify({ detail: "not found" }));
    return true;
  }
  return sendBytes(req, res, cors, buf, type);
}

// -- routes ---------------------------------------------------------------------------------------

/**
 * Answers the run routes; returns false when the route isn't one of them. `send(status, body,
 * headers)` writes JSON with the mock's CORS headers; `stream` replays a scripted run.
 */
export async function runRoutes({ req, res, scenario, route, cors, send, streamRun }) {
  const behaviour = scenario.split("--")[0];
  const refuse = ([status, error, message], extra = {}, headers = {}) => send(status, { error, ...extra, message }, headers);

  if (req.method === "GET" && route === "/v1/runs") {
    if (behaviour === "empty-list") return send(200, []);
    const row = (id, kind, title, status) => ({ id, kind, title, duration: 90, created: "2026-10-28T10:15:00+00:00", status, cost_usd: null, spend: null, cues: null });
    return send(200, [
      row("ed90", "showcase", "ed90.mp4", "published"),
      row(RUNNING_ID, "live", "harbour.mp4", "running"),
      row(FAILED_ID, "live", "lecture.mp4", "failed"),
      row(FORK_ID, "live", "ed90.mp4", "published"),
      row(RUN_ID, "live", "corridor-walk.mp4", "published"),
      row(SAMPLE_ID, "live", "ed90.mp4", "published"),
      row(UPLOAD_ED90_ID, "live", "ed90.mp4", "published"),
    ]);
  }

  const m = route.match(/^\/v1\/runs\/([\w.-]+)(\/.*)?$/);
  if (!m) return false;
  const [, id, rest = ""] = m;

  // a run that stopped: its replay ends in the run's own error event
  if (req.method === "GET" && rest === "/events" && id === FAILED_ID) {
    res.writeHead(200, { "Content-Type": "text/event-stream", ...cors });
    const evs = [
      { type: "run", run_id: id, duration: 74, options: { mode: "standard", voice: "Sofia" }, seq: 0, stage: null, ts: 1 },
      { type: "stage_start", stage: "listen", seq: 1, ts: 2 },
      { type: "error", stage: "listen", kind: "SpeechError", message: "The speech server didn’t answer in time, so the run stopped at listen.", scope: null, seq: 2, ts: 32 },
    ];
    res.end(evs.map((e) => `id: ${e.seq}\nevent: ${e.type}\ndata: ${JSON.stringify(e)}\n\n`).join(""));
    return true;
  }
  if (req.method === "GET" && rest === "/events" && id === RUNNING_ID) {
    await streamRun({ stopAfter: "c002" });
    return true;
  }

  const run = runOf(scenario, id);
  if (!run) {
    if (req.method === "GET" && rest === "") return send(404, { detail: "run not found" });
    if (rest === "/events") return false; // the live stream routes (mock-api.mjs)
    return refuse(ERR.no_run);
  }

  if (req.method === "GET" && rest === "") {
    if (behaviour === "reread500" && (run.version || run.published)) return send(500, { detail: "Internal Server Error" });
    return send(200, detail(run));
  }
  const file = rest.match(/^\/files\/(.+)$/);
  if ((req.method === "GET" || req.method === "HEAD") && file) return serveFile(req, res, cors, run, decodeURIComponent(file[1]));
  const audio = rest.match(/^\/cues\/([\w-]+)\/audio$/);
  if ((req.method === "GET" || req.method === "HEAD") && audio) {
    const c = [...run.cues, ...run.extended].find((x) => x.id === audio[1]);
    const buf = c?.audio_path ? (fromShowcase(`audio/${c.id}.m4a`) ?? fromShowcase("audio/c001.m4a")) : null;
    if (!buf) return send(404, { detail: "not found" });
    return sendBytes(req, res, cors, buf, "audio/mp4");
  }
  if (rest === "/events") return false;

  // -- writes: the owner's token, and recorded runs are read-only
  const writes = /^\/cues\/[\w-]+(\/approve|\/hold)?$|^\/publish$/.test(rest) && (req.method === "POST" || req.method === "PATCH");
  if (!writes) return false;
  if (id === "ed90") return refuse(ERR.read_only);
  const given = (req.headers.authorization ?? "").replace(/^Bearer\s+/i, "");
  if (given !== TOKENS[id]) return refuse(ERR.forbidden);
  if (behaviour === "refuse409") {
    await body(req);
    return send(409, { detail: "Conflict" });
  }

  if (rest === "/publish" && req.method === "POST") {
    await body(req);
    if (!run.dirty) return send(200, detail(run));
    if (behaviour === "overlap") {
      const first = run.cues.find((c) => VOICED.has(c.status));
      return refuse(ERR.overlap, { cue_ids: [first?.id ?? "c001"] });
    }
    await sleep(behaviour === "slow" ? 1500 : 400);
    run.dirty = false;
    run.published += 1;
    return send(200, detail(run));
  }

  const cm = rest.match(/^\/cues\/([\w-]+)(\/approve|\/hold)?$/);
  const cue = [...run.cues, ...run.extended].find((c) => c.id === cm[1]);
  if (!cue) return refuse(ERR.no_cue);
  const extended = run.extended.includes(cue);
  const replace = (next) => {
    const list = extended ? run.extended : run.cues;
    list[list.indexOf(cue)] = next;
    run.dirty = true;
    run.version += 1;
    return send(200, view(run, next));
  };

  if (cm[2] === "/approve" && req.method === "POST") {
    if (cue.status === "approved") return send(200, view(run, cue));
    if (!["verified", "edited", "extended"].includes(cue.status) || !cue.audio_dur)
      return send(409, { error: "invalid_transition", cue_status: cue.status, message: `A ${cue.status} cue cannot be approved. Edit it so it is checked again.` });
    return replace({ ...cue, status: "approved" });
  }
  if (cm[2] === "/hold" && req.method === "POST") {
    const b = await body(req);
    const reason = typeof b?.reason === "string" ? b.reason.replace(/\s+/g, " ").trim() : "";
    const note = reason ? `held by a reviewer: ${reason}` : cue.status === "held" && cue.note ? cue.note : "held by a reviewer";
    return replace({ ...cue, status: "held", note });
  }
  if (!cm[2] && req.method === "PATCH") {
    const b = await body(req);
    const text = typeof b?.text === "string" ? b.text : "";
    if (!text.trim()) return send(422, { error: "invalid", message: "Send the new description as text." });
    const checked = autofix(text);
    const findings = lint(checked);
    const errors = [...new Set(findings.filter((f) => f.severity === "error").map((f) => f.rule))].sort();
    if (errors.length) return send(422, { error: "lint", text: checked, findings, message: `This description breaks ${errors.join(", ")}. Fix it and save again.` });
    await sleep(behaviour === "slow" ? 1500 : 500);
    if (/\bdoor\b/i.test(checked)) {
      return replace({
        ...cue,
        text: checked,
        status: "held",
        claims: [{ text: "a door", verdict: "not_visible", at: Number((cue.start + 0.8).toFixed(2)) }, ...claimsOf(checked, cue.start).slice(1)],
        findings,
        note: "says a door; the frames show no door",
        audio_path: null,
        audio_dur: null,
        tempo: 1.0,
      });
    }
    const gap = run.gaps?.find((g) => g.id === cue.gap_id);
    const dur = Number((words(checked) / 3.0).toFixed(3));
    return replace({
      ...cue,
      text: checked,
      status: "edited",
      claims: claimsOf(checked, cue.start),
      findings,
      note: gap && dur > gap.usable_end - gap.usable_start ? "sped up to 1.08x to fit its pause" : null,
      audio_path: `work/edits/${cue.id}-${run.version + 1}/take.wav`,
      audio_dur: gap ? Math.min(dur, Number((gap.usable_end - gap.usable_start).toFixed(3))) : dur,
      tempo: 1.0,
    });
  }
  return false;
}
