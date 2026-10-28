import { afterEach, describe, expect, it, vi } from "vitest";
import {
  API_BASE,
  ApiError,
  LIMITS,
  budgetEstimate,
  capReached,
  followRun,
  forkRun,
  health,
  parseEvent,
  parseRetryAfter,
  samples,
  startRun,
  toApiError,
  type RunEvent,
} from "../lib/api";

const enc = new TextEncoder();

/** A text/event-stream response whose body arrives in the given byte pieces (and may then break). */
function sse(pieces: (string | Uint8Array)[], opts: { breakAfter?: boolean } = {}): Response {
  const queue = pieces.map((p) => (typeof p === "string" ? enc.encode(p) : p));
  // one piece per read, as a network would deliver them; erroring in start() would drop the queue
  const body = new ReadableStream<Uint8Array>({
    pull(c) {
      const next = queue.shift();
      if (next) c.enqueue(next);
      else if (opts.breakAfter) c.error(new TypeError("network connection was lost"));
      else c.close();
    },
  });
  return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
}

const frame = (ev: Record<string, unknown>) => `id: ${ev.seq}\nevent: ${ev.type}\ndata: ${JSON.stringify(ev)}\n\n`;

const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("parseEvent", () => {
  it("types each event the API sends", () => {
    const run = parseEvent(JSON.stringify({ type: "run", run_id: "live-1", duration: 90, options: { mode: "standard", voice: "Sofia" }, edit_token: "t", seq: 0, ts: 1, stage: null }));
    expect(run).toMatchObject({ type: "run", run_id: "live-1", duration: 90, edit_token: "t", resumed: false, seq: 0 });

    const end = parseEvent(JSON.stringify({ type: "stage_end", stage: "listen", seconds: 3.2, cached: true, words: 212, windows: 6, seq: 3 }));
    expect(end).toMatchObject({ type: "stage_end", stage: "listen", seconds: 3.2, cached: true, info: { words: 212, windows: 6 } });

    const call = parseEvent(JSON.stringify({ type: "call", stage: "watch", role: "watch", model: "nvidia/Nemotron-3.5-Super-VL", tokens_in: 1200, tokens_out: 80, cost_usd: 0.0004, latency_s: 2.1 }));
    expect(call).toMatchObject({ type: "call", stage: "watch", role: "watch", tokens_cached: 0, cost_usd: 0.0004 });

    const cue = parseEvent(JSON.stringify({ type: "cue", id: "c002", status: "held", text: "A door.", start: 12.5, audio_dur: null, note: "the door is not visible", stage: "finalize" }));
    expect(cue).toMatchObject({ type: "cue", id: "c002", status: "held", audio_dur: null, note: "the door is not visible", cached: false });

    const err = parseEvent(JSON.stringify({ type: "error", kind: "BudgetExceeded", message: "The live demo has used its model budget for today.", scope: "day", stage: "plan" }));
    expect(err).toMatchObject({ type: "error", kind: "BudgetExceeded", scope: "day", stage: "plan" });

    const done = parseEvent(
      JSON.stringify({
        type: "done",
        run_id: "live-1",
        summary: { cues: { verified: 3, held: 1 }, coverage: { ratio: 0.8, described: 4, salient_events: 5 }, overlap_seconds: 0, cost_usd: 0.02, wcag: [{ criterion: "1.2.5", level: "AA", name: "AD", status: "needs review", via: "", reasons: ["1 description held for review"] }] },
      }),
    );
    expect(done).toMatchObject({ type: "done", run_id: "live-1", summary: { cues: { verified: 3, held: 1 }, coverage: { ratio: 0.8, described: 4, salient: 5 }, overlapSeconds: 0, costUsd: 0.02 } });
  });

  it("falls back to the SSE event name and refuses what it can't read", () => {
    expect(parseEvent(JSON.stringify({ stage: "plan" }), "stage_start")).toMatchObject({ type: "stage_start", stage: "plan" });
    expect(parseEvent("not json")).toBeNull();
    expect(parseEvent(JSON.stringify({ type: "progress" }))).toBeNull(); // unknown: a newer API
    expect(parseEvent(JSON.stringify({ type: "cue", id: "c1", status: "maybe", start: 1 }))).toBeNull();
    expect(parseEvent(JSON.stringify({ type: "run" }))).toBeNull();
  });
});

describe("refusals", () => {
  it("maps each refusal to its code, message, wait and scope", async () => {
    const cases: [Response, string, number | null][] = [
      [json(402, { error: "budget", scope: "day", message: "The live demo has used its model budget for today. Recorded runs still work." }), "budget", null],
      [json(409, { error: "busy", message: "Another description is running." }, { "retry-after": "60" }), "busy", 60],
      [json(413, { error: "too_large", message: "This file is over 30 MB." }), "too_large", null],
      [json(422, { error: "format", message: "Send an mp4, mov, webm or mkv video." }), "format", null],
      [json(422, { error: "too_long", message: "This video is 121.5 s long." }), "too_long", null],
      [json(422, { error: "unreadable", message: "This file could not be read as a video." }), "unreadable", null],
      [json(429, { error: "rate_limited", message: "Try again in 52 minutes." }, { "retry-after": "3120" }), "rate_limited", 3120],
      [json(503, { error: "uploads_busy", message: "Other videos are being uploaded." }, { "retry-after": "15" }), "uploads_busy", 15],
      [json(408, { error: "timeout", message: "The upload did not arrive within 60 s. Try a smaller file or a faster connection." }), "timeout", null],
      [
        json(429, { error: "one_upload_at_a_time", message: "Your previous upload is still being received. Wait for it to finish." }, { "retry-after": "15" }),
        "one_upload_at_a_time",
        15,
      ],
    ];
    for (const [res, code, wait] of cases) {
      const e = await toApiError(res);
      expect(e).toBeInstanceOf(ApiError);
      expect([e.code, e.retryAfter]).toEqual([code, wait]);
      expect(e.message.length).toBeGreaterThan(5);
    }
    expect((await toApiError(json(402, { error: "budget", scope: "day", message: "x" }))).scope).toBe("day");
  });

  it("falls back on the status when the body isn't the API's", async () => {
    const html = new Response("<html>Service Unavailable</html>", { status: 503 });
    const e = await toApiError(html);
    expect([e.code, e.status]).toEqual(["unavailable", 503]);
    expect(e.message).toBe("The live service is not available right now.");
    expect((await toApiError(json(404, { detail: "run not found" }))).code).toBe("not_found");
    expect((await toApiError(new Response("", { status: 500 }))).code).toBe("server");
    const slow = await toApiError(new Response("", { status: 408 }));
    expect([slow.code, slow.message]).toEqual(["timeout", "The upload did not arrive in time. Try a smaller file or a faster connection."]);
  });

  it("gives the two upload refusals their own words when the API sends none", async () => {
    expect((await toApiError(json(408, { error: "timeout" }))).message).toBe("The upload did not arrive in time. Try a smaller file or a faster connection.");
    expect((await toApiError(json(429, { error: "one_upload_at_a_time" }))).message).toBe(
      "Your previous upload is still being received. Wait for it to finish, then try again.",
    );
  });

  it("reads Retry-After as seconds or a date", () => {
    expect(parseRetryAfter("120")).toBe(120);
    expect(parseRetryAfter(new Date(10_000 + 30_000).toUTCString(), 10_000)).toBe(30);
    expect(parseRetryAfter("soon")).toBeNull();
    expect(parseRetryAfter(null)).toBeNull();
  });
});

describe("health and budget", () => {
  it("reads health, with live_busy when the API sends it", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json(200, { ok: true, speech: false, live_busy: true, budget: { spent_today: 0.4, cap_day: 5, spent_total: null, cap_total: 50 } })),
    );
    expect(await health()).toEqual({
      ok: true,
      speech: false,
      liveBusy: true,
      // an older answer: what it doesn't say is null
      budget: { spentToday: 0.4, capDay: 5, spentTotal: null, capTotal: 50, capRun: null, searchesToday: null, searchesCapDay: null, searchesCapRun: null },
      retention: null,
    });
  });

  it("says the service is unreachable when nothing answers", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("fetch failed"))));
    await expect(health()).rejects.toMatchObject({ code: "unreachable", status: 0 });
  });

  it("finds the cap a run would cross, as the API's spend check does", () => {
    const b = { spentToday: 4.95, capDay: 5, spentTotal: 10, capTotal: 50, capRun: 0.1, searchesToday: 0, searchesCapDay: 60, searchesCapRun: 3 };
    expect(budgetEstimate(90)).toBeCloseTo(0.095);
    expect(capReached(b, budgetEstimate(90))).toBe("day");
    expect(capReached(b, 0.04)).toBeNull();
    expect(capReached({ ...b, spentTotal: 50 }, 0)).toBeNull(); // spent == cap: nothing more fits, but 0 more is fine
    expect(capReached({ ...b, spentTotal: 49.99 }, 0.02)).toBe("total");
    // the engine's rule now: no cap is null; a cap of 0 refuses every call
    expect(capReached({ ...b, capDay: null }, 1)).toBeNull();
    expect(capReached({ ...b, capDay: 0, spentToday: 0 }, 0.02)).toBe("day");
  });
});

describe("startRun", () => {
  const events = [
    { type: "run", run_id: "live-1", duration: 90, options: { mode: "standard", voice: "Sofia" }, seq: 0, ts: 1, stage: null },
    { type: "stage_start", stage: "listen", seq: 1, ts: 2 },
    { type: "cue", stage: "finalize", id: "c001", status: "verified", text: "Un café, s’il vous plaît.", start: 4, audio_dur: 1.8, note: null, seq: 2, ts: 3 },
    { type: "done", stage: null, run_id: "live-1", summary: { cues: { verified: 1 } }, seq: 3, ts: 4 },
  ];

  it("posts the form and streams events in order, whatever the chunking", async () => {
    // cut inside the two-byte "é" and inside a CRLF-free frame boundary
    const bytes = enc.encode(events.map(frame).join(": ping\n\n"));
    const cut = bytes.indexOf(0xc3) + 1;
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(async () => sse([bytes.slice(0, 7), bytes.slice(7, cut), bytes.slice(cut)]));
    vi.stubGlobal("fetch", fetchMock);

    const seen: RunEvent[] = [];
    const end = await startRun({ sample: "ed90" }, { mode: "extended", voice: "Aria" }, (e) => seen.push(e));

    expect(end).toEqual({ end: "done", runId: "live-1", lastSeq: 3 });
    expect(seen.map((e) => e.type)).toEqual(["run", "stage_start", "cue", "done"]);
    expect(seen[2]).toMatchObject({ text: "Un café, s’il vous plaît." });

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe(`${API_BASE}/v1/runs`);
    expect(init.method).toBe("POST");
    const form = init.body as FormData;
    expect([form.get("sample"), form.get("mode"), form.get("voice")]).toEqual(["ed90", "extended", "Aria"]);
  });

  it("drops events it has already seen", async () => {
    const body = [events[0]!, events[1]!, events[1]!, events[0]!, events[3]!].map(frame).join("");
    vi.stubGlobal("fetch", vi.fn(async () => sse([body])));
    const seen: string[] = [];
    await startRun({ sample: "ed90" }, { mode: "standard", voice: "Sofia" }, (e) => seen.push(e.type));
    expect(seen).toEqual(["run", "stage_start", "done"]);
  });

  it("throws a refusal before any stream", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(409, { error: "busy", message: "Another description is running." }, { "retry-after": "60" })));
    const onEvent = vi.fn();
    await expect(startRun({ sample: "ed90" }, { mode: "standard", voice: "Sofia" }, onEvent)).rejects.toMatchObject({
      code: "busy",
      retryAfter: 60,
    });
    expect(onEvent).not.toHaveBeenCalled();
  });

  it("reports a stream cut off mid-run as dropped, with what it saw", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => sse([frame(events[0]!), frame(events[1]!)], { breakAfter: true })));
    const end = await startRun({ sample: "ed90" }, { mode: "standard", voice: "Sofia" }, () => {});
    expect(end).toEqual({ end: "dropped", runId: "live-1", lastSeq: 1 });
  });

  it("rethrows an abort", async () => {
    const ctrl = new AbortController();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_u: string, init: RequestInit) => {
        ctrl.abort();
        throw init.signal?.reason;
      }),
    );
    await expect(startRun({ sample: "x" }, { mode: "standard", voice: "Sofia" }, () => {}, ctrl.signal)).rejects.toMatchObject({ name: "AbortError" });
  });
});

/** Just enough of EventSource to drive followRun. */
class FakeEventSource {
  static CLOSED = 2;
  static last: FakeEventSource | null = null;
  readyState = 0;
  closed = false;
  private listeners = new Map<string, ((e: Event) => void)[]>();
  constructor(readonly url: string) {
    FakeEventSource.last = this;
  }
  addEventListener(type: string, fn: (e: Event) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]);
  }
  close() {
    this.closed = true;
    this.readyState = 2;
  }
  emit(ev: Record<string, unknown>) {
    const e = new MessageEvent(String(ev.type), { data: JSON.stringify(ev), lastEventId: String(ev.seq) });
    for (const fn of this.listeners.get(String(ev.type)) ?? []) fn(e);
  }
  fail(closed: boolean) {
    this.readyState = closed ? 2 : 0;
    for (const fn of this.listeners.get("error") ?? []) fn(new Event("error"));
  }
}

describe("followRun", () => {
  it("follows with EventSource, drops replayed repeats and stops at the last event", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const seen: string[] = [];
    const done = followRun("live-1", (e) => seen.push(`${e.type}:${e.seq}`));
    const es = FakeEventSource.last!;
    expect(es.url).toBe(`${API_BASE}/v1/runs/live-1/events`);
    es.emit({ type: "run", run_id: "live-1", seq: 0 });
    es.emit({ type: "stage_start", stage: "listen", seq: 1 });
    es.fail(false); // the browser reconnects with Last-Event-ID; the API may replay
    es.emit({ type: "stage_start", stage: "listen", seq: 1 });
    es.emit({ type: "error", kind: "RunTimeout", message: "This description took longer than 10 minutes and was stopped.", seq: 2 });
    await expect(done).resolves.toEqual({ end: "error", runId: "live-1", lastSeq: 2 });
    expect(seen).toEqual(["run:0", "stage_start:1", "error:2"]);
    expect(es.closed).toBe(true);
  });

  it("asks the API why when the stream closes for good", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const fetchMock = vi.fn(async () => json(404, { error: "not_found", message: "This run stopped before it finished and cannot resume." }));
    vi.stubGlobal("fetch", fetchMock);
    const done = followRun("live-2", () => {});
    FakeEventSource.last!.fail(true);
    await expect(done).rejects.toMatchObject({ code: "not_found", message: "This run stopped before it finished and cannot resume." });
  });

  it("with the owner's token, reads with fetch and sends it as a Bearer, even where EventSource exists", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    FakeEventSource.last = null;
    const fetchMock = vi.fn(async () =>
      sse([frame({ type: "run", run_id: "live-4", seq: 0, resumed: true }), frame({ type: "done", run_id: "live-4", seq: 1, summary: null })]),
    );
    vi.stubGlobal("fetch", fetchMock);
    const seen: string[] = [];
    await expect(followRun("live-4", (e) => seen.push(e.type), undefined, { token: "tok-owner" })).resolves.toEqual({ end: "done", runId: "live-4", lastSeq: 1 });
    expect(seen).toEqual(["run", "done"]);
    expect(FakeEventSource.last).toBeNull();
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${API_BASE}/v1/runs/live-4/events`);
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok-owner");
  });

  it("with the token, a cut-off stream is read again from the last event seen", async () => {
    vi.useFakeTimers();
    try {
      const fetchMock = vi
        .fn()
        .mockResolvedValueOnce(sse([frame({ type: "run", run_id: "live-5", seq: 0 }), frame({ type: "stage_start", stage: "listen", seq: 1 })], { breakAfter: true }))
        .mockResolvedValueOnce(sse([frame({ type: "stage_start", stage: "listen", seq: 1 }), frame({ type: "done", run_id: "live-5", seq: 2, summary: null })]));
      vi.stubGlobal("fetch", fetchMock);
      const seen: number[] = [];
      const done = followRun("live-5", (e) => seen.push(e.seq ?? -1), undefined, { token: "tok" });
      await vi.runAllTimersAsync();
      await expect(done).resolves.toEqual({ end: "done", runId: "live-5", lastSeq: 2 });
      expect(seen).toEqual([0, 1, 2]);
      expect((fetchMock.mock.calls[1] as unknown as [string])[0]).toBe(`${API_BASE}/v1/runs/live-5/events?after=1`);
    } finally {
      vi.useRealTimers();
    }
  });

  it("with the token, the API's refusal to resume is thrown in its words", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(403, { error: "forbidden", message: "Only the person who started this run can resume it." })));
    await expect(followRun("live-6", () => {}, undefined, { token: "wrong" })).rejects.toMatchObject({
      code: "forbidden",
      message: "Only the person who started this run can resume it.",
    });
  });

  it("without EventSource, reads the same URL with fetch (204: nothing left)", async () => {
    vi.stubGlobal("EventSource", undefined);
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 204 })));
    await expect(followRun("live-3", () => {})).resolves.toEqual({ end: "closed", runId: null, lastSeq: -1 });
  });
});

describe("the live limits", () => {
  it("match the API: uploads up to 60 s and 30 MB, samples up to 45 s", () => {
    expect(LIMITS.maxSeconds).toBe(60);
    expect(LIMITS.maxSampleSeconds).toBe(45);
    expect(LIMITS.maxBytes).toBe(30 * 1024 * 1024);
  });

  it("knows the API's storage refusal", async () => {
    const e = await toApiError(json(503, { error: "storage", message: "The video could not be stored just now. Try again in a minute." }, { "retry-after": "60" }));
    expect(e).toMatchObject({ code: "storage", retryAfter: 60, apiCode: "storage" });
  });
});

describe("the API's samples", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("are read with their metadata: title, length, credit and licence; what doesn't hold is dropped", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        json(200, [
          { id: "ed45", file: "ed45.mp4", title: "Elephants Dream (excerpt)", credit: "Orange Open Movie Team / Blender Foundation, elephantsdream.org", licence: "CC BY 2.5", licence_url: "https://creativecommons.org/licenses/by/2.5/", source_url: "https://commons.wikimedia.org/wiki/File:Elephants_Dream_(2006).webm", excerpt: "1:00-1:44.9", changes: "cut to 44.9 s", duration: 44.917 },
          { id: "tos45", title: "Tears of Steel (excerpt)", duration: "45", credit: 3, licence_url: "javascript:alert(1)" },
          { id: "" },
          "tos45",
        ]),
      ),
    );
    expect(await samples()).toEqual([
      {
        id: "ed45",
        title: "Elephants Dream (excerpt)",
        duration: 44.917,
        credit: "Orange Open Movie Team / Blender Foundation, elephantsdream.org",
        licence: "CC BY 2.5",
        licenceUrl: "https://creativecommons.org/licenses/by/2.5/",
        sourceUrl: "https://commons.wikimedia.org/wiki/File:Elephants_Dream_(2006).webm",
        excerpt: "1:00-1:44.9",
      },
      // what doesn't hold is left out: a length that isn't a number, a credit that isn't text, an address that isn't https
      { id: "tos45", title: "Tears of Steel (excerpt)", duration: null, credit: null, licence: null, licenceUrl: null, sourceUrl: null, excerpt: null },
    ]);
  });

  it("an older API that sends ids only still lists them", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(200, [{ id: "ed45" }])));
    expect(await samples()).toEqual([{ id: "ed45", title: null, duration: null, credit: null, licence: null, licenceUrl: null, sourceUrl: null, excerpt: null }]);
  });
});

describe("studio refusals", () => {
  it("keeps what a refusal carries: the lint findings and checked text, the cues that stopped a publish", async () => {
    const lint = await toApiError(
      json(422, {
        error: "lint",
        text: "We see a door.",
        findings: [
          { rule: "R7-person", message: "first/second person 'We'; describe in third person", span: [0, 2], severity: "error" },
          { rule: "R14-gender", message: "gendered word", span: null, severity: "warning" },
          { message: "no rule: dropped" },
        ],
        message: "This description breaks R7-person. Fix it and save again.",
      }),
    );
    expect(lint.code).toBe("lint");
    expect(lint.detail.text).toBe("We see a door.");
    expect(lint.detail.findings).toEqual([
      { rule: "R7-person", message: "first/second person 'We'; describe in third person", span: [0, 2], severity: "error" },
      { rule: "R14-gender", message: "gendered word", span: null, severity: "warning" },
    ]);
    const overlap = await toApiError(json(409, { error: "overlap", cue_ids: ["c002", "c005"], message: "Nothing was published: …" }));
    expect(overlap).toMatchObject({ code: "overlap", detail: { cueIds: ["c002", "c005"] } });
    const move = await toApiError(json(409, { error: "invalid_transition", cue_status: "held", message: "A held cue cannot be approved." }));
    expect(move.detail.cueStatus).toBe("held");
    // a 403 without the API's body is still a refusal to change the run
    expect((await toApiError(new Response("nope", { status: 403 }))).code).toBe("forbidden");
  });

  it("keeps apart what the API said and what the client guessed from the status", async () => {
    const said = await toApiError(json(409, { error: "busy", message: "This run is still being described." }));
    expect(said).toMatchObject({ apiCode: "busy", said: "This run is still being described." });
    const guessed = await toApiError(json(409, { detail: "Conflict" }));
    // not the API's own refusal: the code is a guess, its words are whatever the server said
    expect(guessed).toMatchObject({ code: "busy", apiCode: null, said: "Conflict" });
    const unknown = await toApiError(json(403, { error: "csrf", message: "Origin not allowed." }));
    expect(unknown).toMatchObject({ code: "forbidden", apiCode: "csrf", said: "Origin not allowed." });
  });
});

describe("forkRun", () => {
  it("posts to the run's fork route and returns the copy's id and token", async () => {
    const fetchMock = vi.fn(async () => json(201, { run_id: "live-20261028-0912-ab12cd", edit_token: "tok-abc" }));
    vi.stubGlobal("fetch", fetchMock);
    expect(await forkRun("ed90")).toEqual({ runId: "live-20261028-0912-ab12cd", editToken: "tok-abc" });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${API_BASE}/v1/runs/ed90/fork`);
    expect(init.method).toBe("POST");
  });

  it("refuses with the API's own words", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json(429, { error: "rate_limited", message: "Too many changes in a minute. Try again in 40 s." }, { "retry-after": "40" })),
    );
    const err = (await forkRun("ed90").catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe("rate_limited");
    expect(err.message).toBe("Too many changes in a minute. Try again in 40 s.");
    expect(err.retryAfter).toBe(40);

    vi.stubGlobal("fetch", vi.fn(async () => json(409, { error: "not_ready", message: "This run has not been published yet, so there is nothing to edit." })));
    const busy = (await forkRun("ed90").catch((e: unknown) => e)) as ApiError;
    expect(busy.status).toBe(409);
    expect(busy.message).toMatch(/not been published/);
  });

  it("says when nothing answers, and when the answer lacks the copy", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("fetch failed"))));
    expect(((await forkRun("ed90").catch((e: unknown) => e)) as ApiError).code).toBe("unreachable");
    vi.stubGlobal("fetch", vi.fn(async () => json(201, { run_id: "x" })));
    expect(((await forkRun("ed90").catch((e: unknown) => e)) as ApiError).code).toBe("server");
  });
});
