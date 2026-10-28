import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { API_BASE, health } from "../lib/api";
import { getRun, listRuns, lookupRun, readDetail } from "../lib/api-runs";

/** The API's own answers, as fixtures. */
const fx = (name: string) => JSON.parse(readFileSync(path.join(__dirname, "fixtures", "api", name), "utf8"));
const DONE = fx("detail-done.json");
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

afterEach(() => vi.unstubAllGlobals());

describe("a run's detail, as the API now gives it", () => {
  it("reads status, source, timeline, loudness and the original from one answer", () => {
    const d = readDetail(DONE)!;
    expect(d).toMatchObject({ id: DONE.id, kind: "live", status: "done", title: "ed45.mp4", duration: 44.917, dirty: false, wps: 3.05, full: true });
    expect(d.source).toEqual({ kind: "sample", sampleId: "ed45", forkedFrom: null, origin: null });
    expect(d.speech?.[0]).toEqual([0.24, 1.68]);
    expect(d.gaps?.[0]).toEqual({ id: "g001", start: 9.29, end: 15.64, capacity: 19 });
    expect(d.loudness).toHaveLength(8);
    expect(d.loudnessHop).toBe(0.1);
    expect(d.original).toBe(`${API_BASE}/v1/runs/${DONE.id}/original`);
    expect(d.cues.map((c) => [c.id, c.extended])).toEqual([["c001", false]]);
    expect(d.extended.map((c) => [c.id, c.extended])).toEqual([["x001", true]]);
  });

  it("spend is model dollars and a count of searches; no search is priced", () => {
    const s = readDetail(DONE)!.manifest!.summary;
    expect(s.spend).toEqual({ modelUsd: 0.03323, modelUsdAllRuns: 0.03323, searches: 1 });
    const search = s.calls?.find((g) => g.model === "tavily-search");
    expect(search).toMatchObject({ calls: 1, searches: 1, costUsd: null });
  });

  it("a fork says what it was forked from and where that came from", () => {
    const d = readDetail({ ...DONE, source: { kind: "fork", forked_from: "ed90", origin: "sample" } })!;
    expect(d.source).toEqual({ kind: "fork", sampleId: null, forkedFrom: "ed90", origin: "sample" });
    expect(readDetail({ ...DONE, source: { kind: "upload" } })!.source).toEqual({ kind: "upload", sampleId: null, forkedFrom: null, origin: null });
  });

  it("an unfinished run, seen by anyone but its owner, is only its id, status, kind of source and time", () => {
    const d = readDetail({ id: "live-x", status: "stopped", source: { kind: "upload" }, created: "2026-10-28T09:00:00+00:00" })!;
    expect(d).toMatchObject({ id: "live-x", status: "stopped", full: false, manifest: null, cues: [], original: null, loudness: null });
    expect(readDetail({ id: "live-y", status: "running", source: { kind: "sample" } })!.status).toBe("running");
  });

  it("loudness and the original may be null; a failed run carries its error", () => {
    const d = readDetail({ ...DONE, loudness: null, original: null, status: "failed", manifest: null, error: { type: "SpeechError", message: "The speech server stopped.", stage: "listen" } })!;
    expect(d).toMatchObject({ loudness: null, original: null, status: "failed", manifest: null });
    expect(d.error).toEqual({ type: "SpeechError", message: "The speech server stopped.", stage: "listen", scope: null });
  });

  it("asks with the owner's token when this browser holds it", async () => {
    const seen: (string | null)[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        seen.push(new Headers(init?.headers).get("authorization"));
        return json(200, DONE);
      }),
    );
    await getRun(DONE.id, { token: "tok-1" });
    await getRun(DONE.id);
    expect(seen).toEqual(["Bearer tok-1", null]);
  });

  it("is looked up by its own status: done, running, stopped, failed with the reason, or missing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/v1/runs/ok")) return json(200, DONE);
        if (url.endsWith("/v1/runs/run")) return json(200, { id: "run", status: "running", source: { kind: "sample" }, created: null });
        if (url.endsWith("/v1/runs/stop")) return json(200, { id: "stop", status: "stopped", source: { kind: "upload" }, created: null });
        if (url.endsWith("/v1/runs/fail")) return json(200, { ...DONE, id: "fail", status: "failed", manifest: null, error: { type: "X", message: "The speech server stopped.", stage: "listen" } });
        return json(404, { error: "not_found", message: "There is no run with that id." });
      }),
    );
    expect((await lookupRun("ok")).kind).toBe("done");
    expect(await lookupRun("run")).toEqual({ kind: "running" });
    expect(await lookupRun("stop")).toEqual({ kind: "stopped" });
    expect(await lookupRun("fail")).toEqual({ kind: "failed", reason: "The speech server stopped." });
    expect(await lookupRun("gone")).toEqual({ kind: "missing" });
  });
});

describe("the run list and health, as the API now gives them", () => {
  it("lists runs by the new status words", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        json(200, [
          { id: "ed90", kind: "showcase", title: "ed90.mp4", status: "done", created: null, spend: { model_usd: 0.03, model_usd_all_runs: null, searches: 0 } },
          { id: "live-a", kind: "live", title: "ed45.mp4", status: "running", created: "2026-10-28T09:00:00+00:00" },
          { id: "live-b", kind: "live", title: "tos45.mp4", status: "stopped", created: null },
          { id: "live-c", kind: "live", title: "bbb45.mp4", status: "published", created: null },
        ]),
      ),
    );
    expect((await listRuns()).map((r) => [r.id, r.status])).toEqual([
      ["ed90", "done"],
      ["live-a", "running"],
      ["live-b", "stopped"],
    ]);
  });

  it("reads the budget's searches and how long uploads and copies are kept", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(200, fx("health.json"))));
    const h = await health();
    expect(h.budget).toMatchObject({ spentToday: 0.0332, capDay: 0.1, capRun: 0.1, searchesToday: 1, searchesCapDay: 60, searchesCapRun: 3 });
    expect(h.retention).toEqual({ uploadDays: 7, forkHours: 24 });
  });
});

describe("web searches in a live run", () => {
  it("a search call is counted and never priced, whatever the engine attached", async () => {
    const { parseEvent } = await import("../lib/api");
    const ev = parseEvent(JSON.stringify({ type: "call", stage: "research", ts: 1, seq: 4, role: "research", model: "tavily-search", provider: "tavily", tokens_in: 0, tokens_out: 0, cost_usd: 0.008, latency_s: 2.6 }));
    expect(ev).toMatchObject({ type: "call", search: true, cost_usd: 0 });
    const model = parseEvent(JSON.stringify({ type: "call", stage: "plan", ts: 1, seq: 5, role: "plan", model: "nvidia/Nemotron-3-Ultra-550b-a55b", provider: "nebius", tokens_in: 10, tokens_out: 5, cost_usd: 0.0146, latency_s: 19 }));
    expect(model).toMatchObject({ search: false, cost_usd: 0.0146 });
  });

  it("the done event's summary gives model dollars and searches apart", async () => {
    const { parseEvent } = await import("../lib/api");
    const ev = parseEvent(JSON.stringify({ type: "done", seq: 30, ts: 2, run_id: "r", seconds: 182, summary: { cost_usd: 0.03323, spend: { model_usd: 0.03323, model_usd_all_runs: 0.03323, searches: 1 } } }));
    expect(ev?.type === "done" && ev.summary).toMatchObject({ costUsd: 0.03323, searches: 1 });
  });
});

describe("the two POSTs that start work", () => {
  it("say they come from this site (X-Lull-Client: web), so the browser asks first and other sites can't start runs", async () => {
    const { startRun, forkRun } = await import("../lib/api");
    const seen: Record<string, string | null> = {};
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        seen[url.endsWith("/fork") ? "fork" : "start"] = new Headers(init?.headers).get("x-lull-client");
        if (url.endsWith("/fork")) return json(201, { run_id: "live-f", edit_token: "t" });
        const body = 'id: 0\nevent: run\ndata: {"type":"run","seq":0,"run_id":"live-a","edit_token":"t"}\n\nid: 1\nevent: done\ndata: {"type":"done","seq":1,"run_id":"live-a","summary":null}\n\n';
        return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
      }),
    );
    await startRun({ sample: "ed45" }, { mode: "standard", voice: "Sofia" }, () => {});
    await forkRun("ed90");
    expect(seen).toEqual({ start: "web", fork: "web" });
  });
});

describe("the API's final fix", () => {
  it("a stopped run ends its stream with a stopped event, said in the API's words", async () => {
    const { parseEvent, followRun } = await import("../lib/api");
    expect(parseEvent(JSON.stringify({ type: "stopped", stage: "watch", seq: 4, ts: 1, message: "The description stopped when the page following it closed." }))).toMatchObject({
      type: "stopped",
      message: "The description stopped when the page following it closed.",
    });
    const body = 'id: 0\nevent: run\ndata: {"type":"run","seq":0,"run_id":"live-a"}\n\nid: 1\nevent: stopped\ndata: {"type":"stopped","seq":1,"stage":"listen","message":"Stopped."}\n\n';
    vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } })));
    const end = await followRun("live-a", () => {}, undefined, { token: "t" });
    expect(end.end).toBe("stopped");
  });

  it("resuming retries a 409 busy for a few seconds while the stopped run's thread finishes", async () => {
    vi.useFakeTimers();
    const { followRun } = await import("../lib/api");
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        calls += 1;
        if (calls < 3) return json(409, { error: "busy", message: "Another description is running." });
        return new Response('id: 5\nevent: done\ndata: {"type":"done","seq":5,"run_id":"live-a","summary":null}\n\n', { status: 200, headers: { "content-type": "text/event-stream" } });
      }),
    );
    const p = followRun("live-a", () => {}, undefined, { token: "t" });
    await vi.runAllTimersAsync();
    expect((await p).end).toBe("done");
    expect(calls).toBe(3);
    vi.useRealTimers();
  });

  it("names a refusal by what it means where it happened", async () => {
    const { refusalWords } = await import("../lib/refusal-words");
    const e = (code: string, status: number, message: string) => ({ code, status, message });
    expect(refusalWords(e("rate_limited", 429, "You have made 10 copies today. Try again tomorrow."), "fork")).toBe("You’ve reached today’s limit for copies.");
    expect(refusalWords(e("rate_limited", 429, "You have made 200 changes today."), "change")).toBe("You’ve reached today’s limit for changes.");
    expect(refusalWords(e("rate_limited", 429, "You have started 3 live descriptions in the last hour."), "start")).toBeNull();
    expect(refusalWords(e("busy", 429, "This run already has 3 viewers."), "follow")).toBe("Too many people are following this run right now.");
    expect(refusalWords(e("busy", 409, "Another change to this run is being made."), "change")).toBe("Another change to this run is in progress. Try again in a moment.");
    expect(refusalWords(e("busy", 409, "Another description is running."), "start")).toBeNull();
    // a bare 409 that the API didn't word (a proxy, an older API) isn't named for it
    expect(refusalWords({ ...e("busy", 409, "Conflict"), apiCode: null }, "change")).toBeNull();
  });

  it("an upload's own name is read from its source", () => {
    expect(readDetail({ ...DONE, source: { kind: "upload", name: "clip.mov" } })!.source).toEqual({ kind: "upload", sampleId: null, forkedFrom: null, origin: null, name: "clip.mov" });
  });
});
