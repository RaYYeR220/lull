/**
 * The REST reference the developer page prints, one entry per route the site calls
 * (lib/api-routes.ts). The facts are the API's own (engine/src/lull/api: app.py, live.py,
 * studio.py, store.py, health.py, ratelimit.py): limits, codes, shapes. The shapes show the
 * fields and their types; the values in them are examples.
 */
import type { RefusalCode } from "./api";
import { ROUTES, type RouteName } from "./api-routes";

export type Field = { name: string; type: string; required: boolean; what: string };

export type Refusal = {
  status: number;
  code: RefusalCode;
  /** When it happens, in plain words. */
  when: string;
  /** The answer carries Retry-After (seconds). */
  retryAfter?: boolean;
};

export type EndpointDoc = {
  group: "Live runs" | "Reading runs" | "Studio";
  summary: string;
  /** "edit-token": Authorization: Bearer <the run's edit token>. */
  auth: "none" | "edit-token" | "edit-token to resume";
  body?: { type: "multipart/form-data" | "application/json"; fields: Field[] };
  /** Path and query parameters. */
  params?: Field[];
  success: { status: number; type: string; what: string; shape: string };
  refusals: Refusal[];
  limits?: string;
  /** A curl call, with $LULL_API standing for the API's address. */
  example: string;
};

/** The body every refusal has: the API's code, its own words, and the code's extras. */
export const REFUSAL_SHAPE = `{
  "error": "rate_limited",
  "message": "You have started 3 live descriptions in the last hour. Try again in 52 minutes, or open a recorded run."
}`;

const CUE_SHAPE = `{
  "id": "c002",
  "gap_id": "g002",
  "start": 18.349,
  "end": null,
  "text": "The two people are surrounded by wires.",
  "event_ids": ["e004"],
  "status": "edited",
  "ahead": false,
  "claims": [{ "text": "two people", "verdict": "supported", "at": 18.95 }],
  "audio_url": "/v1/runs/{id}/cues/c002/audio?v=3f9a1c07d2",
  "audio_dur": 2.322,
  "tempo": 1.0,
  "findings": [{ "rule": "R14-gender", "message": "gendered word; confirm the programme establishes it", "span": [4, 7], "severity": "warning" }],
  "note": null
}`;

const DETAIL_SHAPE = `{
  "manifest": {
    "run_id": "live-20261028-091500-…",
    "created": "2026-10-28T09:16:42+00:00",
    "video": { "name": "lecture.mp4", "duration": 58.2, "sha256": "…" },
    "options": { "mode": "standard", "profile": "education", "voice": "Sofia", "language": "en" },
    "outputs": { "descriptions.vtt": { "sha256": "…", "bytes": 1182 }, "described-audio.m4a": { … }, … },
    "summary": {
      "cues": { "verified": 7, "edited": 0, "approved": 0, "held": 1, "extended": 2 },
      "coverage": { "important_events": 9, "described": 8, "ratio": 0.889, … },
      "overlap_seconds": 0.0,
      "held": [{ "id": "c004", "reason": "…" }],
      "wcag": [{ "criterion": "1.2.5", "level": "AA", "status": "needs review", "via": "…", "reasons": ["1 description held for review"] }],
      "cost_usd": 0.0466,
      "spend": { "models_usd": 0.0466, "tavily_credits": 1.0 },
      …
    },
    "versions": { "engine": "…", "models": ["nvidia/Nemotron-3-Ultra-550b-a55b", …], "tts": "NVIDIA Magpie TTS" }
  },
  "cues": { "run_id": "…", "language": "en", "cues": [ <cue>, … ], "extended": [ <cue>, … ] },
  "dirty": false
}`;

const ID: Field = { name: "id", type: "path", required: true, what: "The run id (letters, digits, . _ -; at most 80)." };
const CUE_ID: Field = { name: "cue_id", type: "path", required: true, what: "The cue id from the run's cues (c002, x001…)." };

const READ_ONLY: Refusal = { status: 403, code: "read_only", when: "The run is a recorded run: make a copy (fork) to edit." };
const FORBIDDEN: Refusal = { status: 403, code: "forbidden", when: "No Authorization: Bearer with this run's edit token." };
const NO_RUN: Refusal = { status: 404, code: "not_found", when: "No run, or no cue, with that id." };
const DESCRIBING: Refusal = { status: 409, code: "busy", when: "The run is still being described.", retryAfter: true };
const NOT_READY: Refusal = { status: 409, code: "not_ready", when: "The run isn't published yet." };
const NOT_EDITABLE: Refusal = { status: 409, code: "not_editable", when: "The run's folder lacks what re-checking needs (its source video or stage records)." };
const EDIT_RATE: Refusal = { status: 429, code: "rate_limited", when: "More than 30 changes a minute from you, or 2,000 a day in all.", retryAfter: true };

export const API_REFERENCE: Record<RouteName, EndpointDoc> = {
  health: {
    group: "Live runs",
    summary: "Whether the live service can take a run: the speech server, a run in progress, and the spend so far against its caps.",
    auth: "none",
    success: {
      status: 200,
      type: "application/json",
      what: "Cached for 10 s, except live_busy. Spend is in USD: model calls and web searches together, then apart.",
      shape: `{
  "ok": true,
  "speech": true,
  "live_busy": false,
  "budget": {
    "cap_day": 3.0, "cap_total": 35.0,
    "spent_today": 0.41, "models_today": 0.40, "tavily_credits_today": 1.0,
    "spent_total": 3.27, "models_total": 3.19, "tavily_credits_total": 9.0
  }
}`,
    },
    refusals: [],
    example: `curl -s "$LULL_API/v1/health"`,
  },
  samples: {
    group: "Live runs",
    summary: "The sample clips the live service can describe: each up to 45 s, with its title, length, credit and licence.",
    auth: "none",
    success: {
      status: 200,
      type: "application/json",
      what: "Each sample's id (pass it as sample to POST /v1/runs), title, length in seconds, credit and licence.",
      shape: `[
  { "id": "csc_intro", "title": "Climate Science Champions, introduction", "duration": 44.9,
    "credit": "U.S. Geological Survey", "licence": "public domain" },
  { "id": "ed45", "title": "Elephants Dream, an excerpt", "duration": 45.0,
    "credit": "Elephants Dream (2006), Blender Foundation / Orange Open Movie Team", "licence": "CC BY 2.5" },
  { "id": "tos45", "title": "Tears of Steel, an excerpt", "duration": 45.0,
    "credit": "Tears of Steel (2012), Blender Foundation", "licence": "CC BY 3.0" }
]`,
    },
    refusals: [],
    example: `curl -s "$LULL_API/v1/samples"`,
  },
  startRun: {
    group: "Live runs",
    summary: "Describe a clip, live: send a file or a sample id and read the run as it happens, as Server-Sent Events.",
    auth: "none",
    body: {
      type: "multipart/form-data",
      fields: [
        { name: "file", type: "file", required: false, what: "The video: mp4, mov, webm or mkv, up to 60 s and 30 MB; longer clips are refused (too_long)." },
        { name: "sample", type: "string", required: false, what: "A sample id instead of a file (samples run up to 45 s)." },
        { name: "mode", type: "standard | extended", required: false, what: "extended also writes long versions for the extended track. Default standard." },
        { name: "voice", type: "Sofia | Aria", required: false, what: "The voice. Default Sofia." },
      ],
    },
    success: {
      status: 200,
      type: "text/event-stream",
      what:
        "First a run event with the run id and its edit token (sent only here, keep it: the studio needs it), then each stage, model call, cue and warning as it happens, then done or error. A “: ping” comment comes every 15 s of quiet.",
      shape: `id: 0
event: run
data: {"type":"run","stage":null,"ts":1761642902.1,"seq":0,"run_id":"live-…","duration":42.0,"options":{"mode":"standard","voice":"Sofia"},"edit_token":"…"}

id: 3
event: stage_end
data: {"type":"stage_end","stage":"listen","seq":3,"seconds":6.4,"cached":false,"words":187,"gaps":5,"windows":4,"capacity_words":140}

id: 9
event: call
data: {"type":"call","stage":"watch","seq":9,"role":"watch","model":"nvidia/Nemotron-3.5-Super-VL","tokens_in":18420,"tokens_out":812,"cost_usd":0.0063,"latency_s":5.42}

id: 21
event: cue
data: {"type":"cue","stage":"finalize","seq":21,"id":"c001","status":"verified","text":"…","start":8.32,"audio_dur":3.1,"note":null}

id: 30
event: done
data: {"type":"done","seq":30,"run_id":"live-…","seconds":85.9,"summary":{"cues":{…},"coverage":{…},"wcag":[…],"cost_usd":0.0466,…}}`,
    },
    refusals: [
      { status: 400, code: "invalid", when: "The body couldn't be read as a form." },
      { status: 400, code: "disconnected", when: "The upload was cut off before it finished." },
      { status: 402, code: "budget", when: "A spend cap is reached (scope: run, day, total, or the provider's balance)." },
      { status: 408, code: "timeout", when: "The whole upload didn't arrive within 60 s." },
      { status: 409, code: "busy", when: "Another description is running: one at a time.", retryAfter: true },
      { status: 413, code: "too_large", when: "The file is over 30 MB." },
      { status: 422, code: "format", when: "Not an mp4, mov, webm or mkv." },
      { status: 422, code: "too_long", when: "Over 60 s (a sample: 45 s)." },
      { status: 422, code: "unreadable", when: "The file can't be read as a video." },
      { status: 422, code: "invalid", when: "Neither or both of file and sample; an unknown mode or voice." },
      { status: 422, code: "sample", when: "No sample with that id." },
      { status: 429, code: "rate_limited", when: "3 runs an hour from you, 20 a day in all, or 10 of your uploads refused in an hour.", retryAfter: true },
      { status: 429, code: "one_upload_at_a_time", when: "Your previous upload is still arriving.", retryAfter: true },
      { status: 503, code: "uploads_busy", when: "Two uploads are already being received.", retryAfter: true },
      { status: 503, code: "storage", when: "The upload couldn't be stored just now (the service's disk, not your file).", retryAfter: true },
    ],
    limits:
      "One run at a time. 3 runs an hour per visitor and 20 a day across visitors; a run is stopped after 10 minutes. The clip goes to Nebius Token Factory (NVIDIA Nemotron models) and to NVIDIA speech models on the service's server; anyone with a run's id can open it. Uploads are deleted after 7 days, forks after 24 hours.",
    example: `curl -N "$LULL_API/v1/runs" -F sample=ed45 -F mode=standard -F voice=Sofia`,
  },
  listRuns: {
    group: "Reading runs",
    summary: "The recorded runs and the 20 most recent runs of the samples, newest first, with where each stands. Uploads are never listed: a run of your own clip opens only by its id.",
    auth: "none",
    success: {
      status: 200,
      type: "application/json",
      what: "status is published, running or failed. Cached for 5 s.",
      shape: `[
  {
    "id": "live-…", "kind": "live", "title": "ed45.mp4", "duration": 45.0,
    "created": "2026-10-28T09:16:42+00:00", "status": "published",
    "cost_usd": 0.0466, "spend": { "models_usd": 0.0466, "tavily_credits": 1.0 },
    "cues": { "verified": 7, "edited": 0, "approved": 0, "held": 1, "extended": 2 }
  }
]`,
    },
    refusals: [],
    example: `curl -s "$LULL_API/v1/runs"`,
  },
  run: {
    group: "Reading runs",
    summary: "A published run: its manifest (files, summary, WCAG statuses, versions) and every cue with its checks.",
    auth: "none",
    params: [ID],
    success: {
      status: 200,
      type: "application/json",
      what: "dirty: changed in the studio since it was last published. Local paths never appear: a cue's take is an audio_url.",
      shape: DETAIL_SHAPE,
    },
    refusals: [{ status: 404, code: "not_found", when: "No published run with that id (one still running has no manifest yet)." }],
    example: `curl -s "$LULL_API/v1/runs/$RUN"`,
  },
  events: {
    group: "Reading runs",
    summary: "A run's events: the replay, then the live stream while it is still going.",
    auth: "edit-token to resume",
    params: [ID, { name: "after", type: "integer", required: false, what: "Only events after this seq (also read from Last-Event-ID)." }],
    success: {
      status: 200,
      type: "text/event-stream",
      what: "The same events POST /v1/runs streamed. 204 when nothing is left after `after`. A run that stopped without finishing is resumed, and only for its owner.",
      shape: `id: 31
event: done
data: {"type":"done","seq":31,…}`,
    },
    refusals: [
      { status: 403, code: "forbidden", when: "Resuming an unfinished run needs its edit token as a Bearer." },
      { status: 404, code: "not_found", when: "No run with that id, or no progress was recorded for it." },
      { status: 409, code: "busy", when: "Resuming, while another run is going.", retryAfter: true },
      { status: 402, code: "budget", when: "Resuming, past a spend cap." },
    ],
    example: `curl -N "$LULL_API/v1/runs/$RUN/events?after=10"`,
  },
  file: {
    group: "Reading runs",
    summary: "One of the run's files: any output its manifest lists, or keyframes/<cue>.jpg. HEAD works too.",
    auth: "none",
    params: [ID, { name: "path", type: "path", required: true, what: "descriptions.vtt, extended.vtt, described-audio.m4a, described.mp4, extended.mp4, script.md, report.html, cues.json, manifest.json, keyframes/c001.jpg" }],
    success: {
      status: 200,
      type: "the file's own type",
      what: "report.html is served with a Content-Security-Policy that allows no scripts.",
      shape: `WEBVTT
Kind: descriptions
Language: en

c001
00:00:08.320 --> 00:00:11.420
In a dim corridor of cables, …`,
    },
    refusals: [{ status: 404, code: "not_found", when: "Not one of the run's outputs or keyframes." }],
    example: `curl -sO "$LULL_API/v1/runs/$RUN/files/descriptions.vtt"`,
  },
  cueAudio: {
    group: "Reading runs",
    summary: "One cue's own recording (WAV): the take the mix used, or an edit's new take. HEAD works too.",
    auth: "none",
    params: [ID, CUE_ID],
    success: { status: 200, type: "audio/wav", what: "Each cue's audio_url carries ?v=<tag>, which changes with each new take.", shape: "(audio)" },
    refusals: [{ status: 404, code: "not_found", when: "No such cue, or it has no take (a held line)." }],
    example: `curl -sO "$LULL_API/v1/runs/$RUN/cues/c001/audio"`,
  },
  editCue: {
    group: "Studio",
    summary: "Change a line's text. It is linted first; then the line is checked against its frames, fitted and voiced again.",
    auth: "edit-token",
    params: [ID, CUE_ID],
    body: { type: "application/json", fields: [{ name: "text", type: "string", required: true, what: "The new line, 1 to 1,000 characters." }] },
    success: { status: 200, type: "application/json", what: "The cue as stored: edited, or held with the reason in its note.", shape: CUE_SHAPE },
    refusals: [
      READ_ONLY,
      FORBIDDEN,
      NO_RUN,
      DESCRIBING,
      NOT_READY,
      NOT_EDITABLE,
      { status: 413, code: "too_large", when: "The body is over 64 KB." },
      { status: 422, code: "invalid", when: "No text, or over 1,000 characters." },
      { status: 422, code: "lint", when: "The text breaks a writing rule: the answer has the findings and the text as checked; nothing else ran." },
      { status: 402, code: "budget", when: "A spend cap is reached (an edit reserves about $0.01)." },
      EDIT_RATE,
      { status: 502, code: "engine", when: "The check itself failed; the line is left as it was." },
      { status: 503, code: "overloaded", when: "Two edits are already being checked.", retryAfter: true },
    ],
    limits: "30 changes a minute per visitor, 2,000 a day in all; one change to a run at a time.",
    example: `curl -s -X PATCH "$LULL_API/v1/runs/$RUN/cues/c002" \\
  -H "Authorization: Bearer $EDIT_TOKEN" -H "Content-Type: application/json" \\
  -d '{"text":"Two people stand among hanging wires."}'`,
  },
  approveCue: {
    group: "Studio",
    summary: "Sign a line off: a verified, edited or voiced extended line becomes approved.",
    auth: "edit-token",
    params: [ID, CUE_ID],
    success: { status: 200, type: "application/json", what: "The cue, approved (approving it again changes nothing).", shape: `{ "id": "c002", "status": "approved", … }` },
    refusals: [
      READ_ONLY,
      FORBIDDEN,
      NO_RUN,
      DESCRIBING,
      NOT_READY,
      { status: 409, code: "invalid_transition", when: "A held line, or one without a take: edit it first. The answer says its cue_status." },
      EDIT_RATE,
    ],
    example: `curl -s -X POST "$LULL_API/v1/runs/$RUN/cues/c002/approve" -H "Authorization: Bearer $EDIT_TOKEN"`,
  },
  holdCue: {
    group: "Studio",
    summary: "Hold a line back for review, with a reason. A held line isn't voiced or mixed.",
    auth: "edit-token",
    params: [ID, CUE_ID],
    body: { type: "application/json", fields: [{ name: "reason", type: "string", required: false, what: "Why, up to 300 characters; the note becomes “held by a reviewer: <reason>”." }] },
    success: { status: 200, type: "application/json", what: "The cue, held.", shape: `{ "id": "c002", "status": "held", "note": "held by a reviewer: the name is wrong", … }` },
    refusals: [READ_ONLY, FORBIDDEN, NO_RUN, DESCRIBING, NOT_READY, { status: 422, code: "invalid", when: "The reason isn't text, or is over 300 characters." }, EDIT_RATE],
    example: `curl -s -X POST "$LULL_API/v1/runs/$RUN/cues/c002/hold" \\
  -H "Authorization: Bearer $EDIT_TOKEN" -H "Content-Type: application/json" -d '{"reason":"the name is wrong"}'`,
  },
  publish: {
    group: "Studio",
    summary: "Publish the lines as they are now: the overlap gate, then every file written again. Nothing is written when the gate refuses.",
    auth: "edit-token",
    params: [ID],
    success: { status: 200, type: "application/json", what: "The run as GET /v1/runs/{id} gives it, dirty false. Publishing an unchanged run does nothing.", shape: DETAIL_SHAPE },
    refusals: [
      READ_ONLY,
      FORBIDDEN,
      NO_RUN,
      DESCRIBING,
      NOT_READY,
      NOT_EDITABLE,
      { status: 409, code: "overlap", when: "A line would overlap speech, the edge of its pause or another line: cue_ids names them." },
      { status: 409, code: "not_voiced", when: "A line that would be voiced has lost its take: cue_ids names them." },
      { status: 429, code: "rate_limited", when: "4 publishes a minute from you, 500 a day in all.", retryAfter: true },
      { status: 503, code: "overloaded", when: "Another run is being published.", retryAfter: true },
      { status: 500, code: "publish_failed", when: "Writing the files failed; the last published version stays." },
    ],
    example: `curl -s -X POST "$LULL_API/v1/runs/$RUN/publish" -H "Authorization: Bearer $EDIT_TOKEN"`,
  },
  fork: {
    group: "Studio",
    summary: "Copy a published run (a recorded one too) into a new run you can edit. No model is called. Copies are removed a day after they are made.",
    auth: "none",
    params: [ID],
    success: { status: 201, type: "application/json", what: "The copy's id and its edit token (keep it: it is the only way to change the copy).", shape: `{ "run_id": "live-…", "edit_token": "…" }` },
    refusals: [
      NO_RUN,
      DESCRIBING,
      NOT_READY,
      { status: 429, code: "rate_limited", when: "5 copies an hour from you, 100 a day in all.", retryAfter: true },
      { status: 500, code: "fork_failed", when: "The copy couldn't be made." },
    ],
    example: `curl -s -X POST "$LULL_API/v1/runs/ed90/fork"`,
  },
};

/** The routes in the order the page lists them: by group, as the table has them. */
export const REFERENCE_ORDER: RouteName[] = Object.keys(ROUTES) as RouteName[];
