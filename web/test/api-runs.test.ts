import { afterEach, describe, expect, it, vi } from "vitest";
import { API_BASE, ApiError } from "../lib/api";
import {
  approveCue,
  cueAudioPattern,
  editCue,
  getRun,
  holdCue,
  lookupRun,
  publishRun,
  readCue,
  readDetail,
  runFileUrl,
} from "../lib/api-runs";

const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const CUE = {
  id: "c002",
  gap_id: "g002",
  start: 18.349,
  end: null,
  text: "The two people are surrounded by wires.",
  event_ids: ["e02"],
  status: "verified",
  ahead: false,
  claims: [
    { text: "two people", verdict: "supported", at: 18.9 },
    { text: "a walkway", verdict: "not_visible", at: null },
    { text: "odd", verdict: "maybe" },
  ],
  audio_url: "/v1/runs/r1/cues/c002/audio?v=abc",
  audio_dur: 2.322,
  tempo: 1.0,
  findings: [{ rule: "R14-gender", message: "gendered word", span: [4, 7], severity: "warning" }],
  note: "removed “a walkway”",
};

const DETAIL = {
  manifest: {
    run_id: "r1",
    created: "2026-10-27T21:46:38+00:00",
    video: { name: "ed90.mp4", duration: 90, sha256: "a" },
    options: { mode: "standard", voice: "Sofia", language: "en" },
    outputs: { "descriptions.vtt": { sha256: "x", bytes: 1182 }, "cues.json": { sha256: "y", bytes: 9000 } },
    summary: {
      cues: { verified: 3, held: 1, edited: 0, approved: 0, extended: 0 },
      coverage: { important_events: 6, described: 5, ratio: 0.833, described_with_extended: 5, ratio_with_extended: 0.833, undescribed: ["e07"] },
      repetitive: [{ id: "e09", text: "Wires sway." }],
      overlap: { speech_s: 0, between_cues_s: 0, outside_pauses_s: 0, total_s: 0 },
      held: [{ id: "c003", reason: "no door" }],
      wcag: [{ criterion: "1.2.5", level: "AA", name: "Audio Description", status: "needs review", via: "described.mp4", reasons: ["1 description held for review"] }],
      cost_usd: 0.0213,
      calls_by_role_model: [{ role: "watch", model: "nvidia/Nemotron-3.5-Super-VL", calls: 3, cost_usd: 0.01 }],
      reviewed: false,
    },
    versions: { engine: "0.4.0", models: ["m"], tts: "Magpie TTS Multilingual" },
  },
  cues: { run_id: "r1", language: "en", cues: [CUE, { id: "bad" }], extended: [] },
  dirty: true,
};

describe("reading a run", () => {
  it("reads a cue: claims with known verdicts only, findings, its take as a URL", () => {
    const cue = readCue(CUE)!;
    expect(cue).toMatchObject({ id: "c002", gapId: "g002", status: "verified", audioDur: 2.322, audioUrl: "/v1/runs/r1/cues/c002/audio?v=abc", note: "removed “a walkway”" });
    expect(cue.claims).toEqual([
      { text: "two people", verdict: "supported", at: 18.9 },
      { text: "a walkway", verdict: "not_visible", at: null },
    ]);
    expect(cue.findings).toEqual([{ rule: "R14-gender", message: "gendered word", span: [4, 7], severity: "warning" }]);
    expect(readCue({ id: "x", start: 1, status: "unknown" })).toBeNull();
  });

  it("reads the detail and its summary, leaving out what it doesn't have", () => {
    const d = readDetail(DETAIL)!;
    expect(d.dirty).toBe(true);
    expect(d.cues.map((c) => c.id)).toEqual(["c002"]);
    expect(d.status).toBe("done"); // an older answer without a status: its manifest says it is done
    expect(d.manifest!.outputs["descriptions.vtt"]).toEqual({ bytes: 1182 });
    const s = d.manifest!.summary;
    expect(s.coverage).toMatchObject({ important: 6, described: 5, ratio: 0.833 });
    expect(s.repetitive).toEqual([{ id: "e09", text: "Wires sway." }]);
    expect(s.wcag?.[0]).toMatchObject({ criterion: "1.2.5", status: "needs review", reasons: ["1 description held for review"] });
    expect(s.overlap?.speech).toBe(0);
    expect(s.f113).toBeNull();
    expect(s.lintWarnings).toBeNull();
    // an older summary keeps only the ids of what the plan judged repetitive
    const older = readDetail({ ...DETAIL, manifest: { ...DETAIL.manifest, summary: { coverage: { salient_events: 4, described: 4, repetitive: ["e1"] } } } })!;
    expect(older.manifest!.summary.repetitive).toEqual([{ id: "e1", text: null }]);
    expect(older.manifest!.summary.coverage?.important).toBe(4);
    expect(readDetail({ cues: {} })).toBeNull();
  });

  it("names the API's file and audio routes", () => {
    expect(runFileUrl("r1", "keyframes/c001.jpg")).toBe(`${API_BASE}/v1/runs/r1/files/keyframes/c001.jpg`);
    expect(cueAudioPattern("r1", 0)).toBe(`${API_BASE}/v1/runs/r1/cues/{id}/audio`);
    expect(cueAudioPattern("r1", 3)).toBe(`${API_BASE}/v1/runs/r1/cues/{id}/audio?v=3`);
  });

  it("gets a run, and says not_found for one that isn't there", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(200, DETAIL)));
    expect((await getRun("r1")).manifest!.runId).toBe("r1");
    vi.stubGlobal("fetch", vi.fn(async () => json(404, { error: "not_found", message: "There is no run with that id." })));
    await expect(getRun("r1")).rejects.toMatchObject({ code: "not_found" });
  });

  it("can't look a run up when nothing answers", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("fetch failed"))));
    await expect(lookupRun("x")).rejects.toMatchObject({ code: "unreachable" });
  });
});

describe("the studio's writes", () => {
  it("send the owner's token, the text as JSON, and read the cue back", async () => {
    const fetchMock = vi.fn(async () => json(200, { ...CUE, status: "edited" }));
    vi.stubGlobal("fetch", fetchMock);
    const cue = await editCue("r1", "c002", "Two people stand among wires.", "tok");
    expect(cue.status).toBe("edited");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${API_BASE}/v1/runs/r1/cues/c002`);
    expect(init.method).toBe("PATCH");
    expect(init.headers).toMatchObject({ Authorization: "Bearer tok", "Content-Type": "application/json" });
    expect(JSON.parse(init.body as string)).toEqual({ text: "Two people stand among wires." });

    await approveCue("r1", "c002", "tok");
    expect((fetchMock.mock.calls[1] as unknown as [string, RequestInit])[0]).toBe(`${API_BASE}/v1/runs/r1/cues/c002/approve`);
    await holdCue("r1", "c002", "tok", "  the name is wrong ");
    const hold = fetchMock.mock.calls[2] as unknown as [string, RequestInit];
    expect(hold[0]).toBe(`${API_BASE}/v1/runs/r1/cues/c002/hold`);
    expect(JSON.parse(hold[1].body as string)).toEqual({ reason: "the name is wrong" });
    await holdCue("r1", "c002", "tok");
    expect((fetchMock.mock.calls[3] as unknown as [string, RequestInit])[1].body).toBeUndefined();
  });

  it("refuse in the API's words, keeping the lint findings", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        json(422, { error: "lint", text: "We see wires.", findings: [{ rule: "R7-person", message: "first person", span: [0, 2], severity: "error" }], message: "This description breaks R7-person." }),
      ),
    );
    const err = (await editCue("r1", "c002", "we see wires", "tok").catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe("lint");
    expect(err.detail.findings?.[0]?.rule).toBe("R7-person");
  });

  it("publish returns the run, or the overlap gate's cues", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(200, { ...DETAIL, dirty: false })));
    expect((await publishRun("r1", "tok")).dirty).toBe(false);
    vi.stubGlobal("fetch", vi.fn(async () => json(409, { error: "overlap", cue_ids: ["c005"], message: "Nothing was published." })));
    await expect(publishRun("r1", "tok")).rejects.toMatchObject({ code: "overlap", detail: { cueIds: ["c005"] } });
  });
});
