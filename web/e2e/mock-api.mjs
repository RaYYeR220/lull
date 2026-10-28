// A stand-in for the Lull API in the e2e tests: the live routes with scripted answers.
//
// Every path starts with a scenario: /<scenario>/v1/... The tests point the page's API
// address at http://127.0.0.1:<port>/<scenario>, so tests running side by side never share
// state. A run's stream follows the real API's framing (id, event, data with type/stage/ts/seq,
// ": ping" comments) and its events follow the engine's order.
//
//   health:  ok (default), spent (today's cap reached), busy (live_busy), nospeech
//   POST /v1/runs:
//     run   the whole run, an event every few tens of ms, ending in done
//     mid   the run up to two cues into finalize, then pings until the client leaves
//     fail  stops in plan with a budget error event
//     drop  cuts the connection after listen; GET .../events then replays the whole run
//     402 409 413 422-format 422-too_long 422-unreadable 429 503
//     408 429-one_upload                                         the API's JSON refusals
//   GET /v1/runs/<id>/events: replays the whole run (the drop and reload cases)
//     resume: the run stopped before it finished, so only its owner may follow it: the edit
//     token as a Bearer, 403 forbidden without it
//   POST /v1/runs/<id>/fork: 201 {run_id, edit_token} for ed90 (FORK_ID); 404 for any other id;
//     fork429: the API's rate-limit refusal
//   the published runs, their files and the studio's writes: e2e/mock-runs.mjs
//
// Run: node e2e/mock-api.mjs [port]   (MOCK_API_PORT also works; default 3218)

import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import { FORK_ID, FORK_TOKEN, RUN_ID, RUN_TOKEN, runRoutes } from "./mock-runs.mjs";

const PORT = Number(process.argv[2] ?? process.env.MOCK_API_PORT ?? 3218);
const STEP_MS = Number(process.env.MOCK_STEP_MS ?? 25);

const PRICES = {
  "nvidia/Nemotron-3-Ultra-550b-a55b": [1.0, 3.0],
  "nvidia/Nemotron-3.5-Super-VL": [0.3, 0.9],
  "nvidia/Nemotron-3_5-Lightning": [0.06, 0.24],
};
const cost = (model, tin, tout) => {
  const p = PRICES[model];
  return p ? Number(((tin * p[0] + tout * p[1]) / 1e6).toFixed(6)) : 0.008;
};
const VL = "nvidia/Nemotron-3.5-Super-VL";
const ULTRA = "nvidia/Nemotron-3-Ultra-550b-a55b";
const LIGHT = "nvidia/Nemotron-3_5-Lightning";

/** [seconds into the run, type, stage, data] */
function script() {
  const call = (t, stage, role, model, tin, tout, latency) => [
    t,
    "call",
    stage,
    { role, model, provider: model.startsWith("nvidia/") ? "tokenfactory" : "tavily", tokens_in: tin, tokens_cached: 0, tokens_out: tout, cost_usd: cost(model, tin, tout), latency_s: latency },
  ];
  const held = { id: "c003", status: "held", text: "A metal door swings open onto a bright white room.", start: 71.24, audio_dur: null, note: "says a door opens; the frames show no door", cached: false };
  const events = [
    [0, "run", null, { run_id: RUN_ID, duration: 90, options: { mode: "standard", voice: "Sofia" }, edit_token: RUN_TOKEN }],
    [0.1, "stage_start", "listen", {}],
    [6.5, "stage_end", "listen", { seconds: 6.412, cached: false, words: 187, speakers: 2, speech_s: 41.2, gaps: 5, windows: 4, capacity_words: 140, clamped: 0, has_audio: true }],
    [6.6, "stage_start", "watch", {}],
    call(12.1, "watch", "watch", VL, 18420, 812, 5.42),
    call(18.9, "watch", "watch", VL, 17980, 766, 6.71),
    call(23.3, "watch", "watch", VL, 9120, 403, 4.35),
    [23.4, "stage_end", "watch", { seconds: 16.8, cached: false, events: 14 }],
    [23.5, "stage_start", "research", {}],
    call(24.9, "research", "research", LIGHT, 2210, 180, 1.38),
    call(25.6, "research", "research", "tavily-search", 0, 0, 0.71),
    [27.4, "stage_end", "research", { seconds: 3.9, cached: false, terms: 3, skipped: false, errors: 0 }],
    [27.5, "stage_start", "plan", {}],
    call(46.1, "plan", "plan", ULTRA, 9840, 1630, 18.52),
    [46.2, "stage_end", "plan", { seconds: 18.7, cached: false, variants: 11, events: 9, cast: ["Emo", "Proog"] }],
    [46.3, "stage_start", "schedule", {}],
    [46.5, "stage_end", "schedule", { seconds: 0.21, cached: false, placed: 4, extended: [], utilisation: 0.62 }],
    [46.5, "warning", "schedule", { kind: "undescribed", message: "1 salient event has no description from the plan and stays undescribed: e07", event_ids: ["e07"] }],
    [46.6, "stage_start", "finalize", {}],
    call(49.8, "finalize", "verify", VL, 6420, 118, 3.12),
    [52.1, "cue", "finalize", { id: "c001", status: "verified", text: "In a dim corridor of cables, an older man, Proog, leads a younger one, Emo.", start: 8.32, audio_dur: 3.1, note: null, cached: false }],
    call(55.0, "finalize", "verify", VL, 6610, 131, 2.94),
    call(56.2, "finalize", "remove", LIGHT, 880, 64, 1.08),
    [58.4, "cue", "finalize", { id: "c002", status: "verified", text: "Proog pulls Emo back from the edge as the floor drops away into darkness.", start: 35.9, audio_dur: 4.4, note: "removed “red” (not visible in the frames)", cached: false }],
    // mid stops here
    call(61.7, "finalize", "verify", VL, 6390, 125, 3.27),
    [62.0, "cue", "finalize", held],
    [62.0, "warning", "finalize", { kind: "held", message: `cue c003 held: ${held.note}`, cue_id: "c003", reason: held.note }],
    call(65.2, "finalize", "verify", VL, 6550, 109, 3.01),
    [67.3, "cue", "finalize", { id: "c004", status: "verified", text: "Emo stares up at a wall of switches, unsure.", start: 81.4, audio_dur: 2.6, note: null, cached: false }],
    [77.8, "stage_end", "finalize", { seconds: 31.2, cached: false, cues: 4, verified: 3, held: 1, extended: 0 }],
    [77.9, "stage_start", "publish", {}],
    [85.8, "stage_end", "publish", { seconds: 7.9, cached: false, files: 14 }],
  ];
  // model spend only: a search is counted, never priced (lull.api.spend)
  const calls = events.filter((e) => e[1] === "call");
  const spent = calls.filter((e) => e[3].provider !== "tavily").reduce((n, e) => n + e[3].cost_usd, 0);
  const searches = calls.filter((e) => e[3].provider === "tavily").length;
  events.push([
    86.0,
    "done",
    null,
    {
      run_id: RUN_ID,
      seconds: 85.9,
      summary: {
        cues: { verified: 3, edited: 0, approved: 0, held: 1, extended: 0 },
        coverage: { salient_events: 6, described: 5, ratio: 0.833, described_with_extended: 5, ratio_with_extended: 0.833, undescribed: ["e07"] },
        overlap_seconds: 0,
        held: [{ id: "c003", reason: held.note }],
        f113: [],
        cost_usd: Number(spent.toFixed(6)),
        cost_per_min: Number((spent / 1.5).toFixed(6)),
        spend: { model_usd: Number(spent.toFixed(6)), model_usd_all_runs: Number(spent.toFixed(6)), searches },
        wcag: [
          { criterion: "1.2.3", level: "A", name: "Audio Description or Media Alternative (Prerecorded)", status: "needs review", via: "descriptions.vtt, script.md", reasons: ["1 description held for review"] },
          { criterion: "1.2.5", level: "AA", name: "Audio Description (Prerecorded)", status: "needs review", via: "described.mp4 and described-audio.m4a", reasons: ["1 description held for review"] },
          { criterion: "1.2.7", level: "AAA", name: "Extended Audio Description (Prerecorded)", status: "not claimed", via: "", reasons: ["extended version not produced"] },
        ],
        extended_produced: false,
      },
    },
  ]);
  return events;
}

const MID_AFTER = "c002";
const FAIL_AT = "plan";
const DROP_AFTER = "listen";

function frames(base) {
  return script().map(([t, type, stage, data], seq) => {
    const ev = { ...data, type, stage, ts: base + t, seq };
    return { ev, text: `id: ${seq}\nevent: ${type}\ndata: ${JSON.stringify(ev)}\n\n` };
  });
}

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Expose-Headers": "Retry-After",
};

function json(res, status, body, headers = {}) {
  res.writeHead(status, { "Content-Type": "application/json", ...CORS, ...headers });
  res.end(JSON.stringify(body));
}

const REFUSALS = {
  402: [402, { error: "budget", scope: "day", message: "The live demo has used its model budget for today. Recorded runs still work." }],
  409: [409, { error: "busy", message: "Another description is running. Try again in a minute, or open a recorded run." }, { "Retry-After": "2" }],
  413: [413, { error: "too_large", message: "This file is over 30 MB. Live descriptions take files up to 30 MB." }],
  "422-format": [422, { error: "format", message: "Send an mp4, mov, webm or mkv video." }],
  "422-too_long": [422, { error: "too_long", message: "This video is 61.5 s long. Live descriptions take videos up to 60 s." }],
  "422-unreadable": [422, { error: "unreadable", message: "This file could not be read as a video. Send an mp4, mov, webm or mkv video." }],
  429: [429, { error: "rate_limited", message: "You have started 3 live descriptions in the last hour. Try again in 52 minutes, or open a recorded run." }, { "Retry-After": "3120" }],
  503: [503, { error: "uploads_busy", message: "Other videos are being uploaded right now. Try again in a few seconds." }, { "Retry-After": "15" }],
  408: [408, { error: "timeout", message: "The upload did not arrive within 60 s. Try a smaller file or a faster connection." }],
  "429-one_upload": [429, { error: "one_upload_at_a_time", message: "Your previous upload is still being received. Wait for it to finish." }, { "Retry-After": "2" }],
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function stream(req, res, { stopAfter, failAt, dropAfter, step = STEP_MS } = {}) {
  res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache, no-transform", ...CORS });
  let open = true;
  res.on("close", () => (open = false)); // the client left (the request's own close fires once its body is read)
  const base = Date.now() / 1000;
  for (const { ev, text } of frames(base)) {
    if (!open) return;
    if (failAt && ev.type === "stage_start" && ev.stage === failAt) {
      res.write(text);
      await sleep(step);
      const err = { type: "error", stage: failAt, kind: "BudgetExceeded", message: "The live demo has used its model budget for today. Recorded runs still work.", scope: "day", ts: ev.ts + 2, seq: ev.seq + 1 };
      res.end(`id: ${err.seq}\nevent: error\ndata: ${JSON.stringify(err)}\n\n`);
      return;
    }
    res.write(text);
    await sleep(step);
    if (dropAfter && ev.type === "stage_end" && ev.stage === dropAfter) {
      res.socket?.destroy();
      return;
    }
    if (stopAfter && ev.type === "cue" && ev.id === stopAfter) {
      // hold the run here: a heartbeat now and then, as the API sends when quiet
      while (open) {
        res.write(": ping\n\n");
        await sleep(1000);
      }
      return;
    }
  }
  res.end();
}

function drain(req) {
  return new Promise((resolve) => {
    req.on("data", () => {});
    req.on("end", resolve);
    req.on("error", resolve);
  });
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", "http://mock");
  const m = url.pathname.match(/^\/([\w-]+)(\/v1\/.*)$/);
  if (req.method === "OPTIONS") {
    // as the API's CORS middleware: Authorization has to be named, a wildcard doesn't cover it
    res.writeHead(204, {
      ...CORS,
      "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS, POST, PATCH",
      "Access-Control-Allow-Headers": "Authorization, Content-Type, Last-Event-ID",
    });
    return res.end();
  }
  if (!m) return json(res, 404, { detail: "Not Found" });
  const [, scenario, route] = m;

  if (req.method === "GET" && route === "/v1/health") {
    // /v1/health as app.py answers: model spend against its caps, searches counted, retention
    const budget = { spent_today: 0.412, cap_day: 5, spent_total: 3.274, cap_total: 50, searches_today: 4, searches_cap_day: 60, cap_run: 0.1, searches_cap_run: 3 };
    if (scenario === "spent") budget.spent_today = 5;
    return json(res, 200, { ok: true, speech: scenario !== "nospeech", budget, live_busy: scenario === "busy", retention_days: 7, fork_retention_hours: 24 });
  }
  if (req.method === "GET" && route === "/v1/samples") return json(res, 200, SAMPLES);

  if (req.method === "POST" && route === "/v1/runs") {
    await drain(req);
    const refusal = REFUSALS[scenario];
    if (refusal) return json(res, refusal[0], refusal[1], refusal[2]);
    if (scenario === "mid") return stream(req, res, { stopAfter: MID_AFTER });
    if (scenario === "fail") return stream(req, res, { failAt: FAIL_AT });
    if (scenario === "drop") return stream(req, res, { dropAfter: DROP_AFTER });
    return stream(req, res);
  }

  const fork = route.match(/^\/v1\/runs\/([\w.-]+)\/fork$/);
  if (req.method === "POST" && fork) {
    await drain(req);
    if (scenario === "fork429")
      return json(res, 429, { error: "rate_limited", message: "Too many changes in a minute. Try again in 40 s." }, { "Retry-After": "40" });
    if (fork[1] !== "ed90") return json(res, 404, { error: "not_found", message: "There is no run with that id." });
    return json(res, 201, { run_id: FORK_ID, edit_token: FORK_TOKEN });
  }

  const send = (status, body, headers = {}) => {
    json(res, status, body, headers);
    return true;
  };
  const streamRun = (opts) => stream(req, res, opts);
  if (await runRoutes({ req, res, scenario, route, cors: CORS, send, streamRun })) return;

  const follow = route.match(/^\/v1\/runs\/([\w.-]+)\/events$/);
  if (req.method === "GET" && follow) {
    if (follow[1] !== RUN_ID) return json(res, 404, { error: "not_found", message: "There is no run with that id." });
    // a run that stopped before it finished: following it resumes it, which only its owner may
    if (scenario.split("--")[0] === "resume" && req.headers.authorization !== `Bearer ${RUN_TOKEN}`)
      return json(res, 403, { error: "forbidden", message: "Only the person who started this run can resume it." });
    const after = Number(url.searchParams.get("after") ?? req.headers["last-event-id"] ?? -1);
    if (after >= frames(0).length - 1) {
      res.writeHead(204, CORS);
      return res.end();
    }
    return stream(req, res, { step: 5 });
  }

  json(res, 404, { detail: "Not Found" });
});

/**
 * The API's samples as GET /v1/samples serves them: its engine/samples/samples.json entries
 * (bbb45, ed45, tos45), the fields it keeps, sorted by id.
 */
const SAMPLES = JSON.parse(readFileSync(new URL("./fixtures/api-samples.json", import.meta.url), "utf8"));

server.listen(PORT, "127.0.0.1", () => {
  console.log(`mock api on http://127.0.0.1:${PORT}`);
});
