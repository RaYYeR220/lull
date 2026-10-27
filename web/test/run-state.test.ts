import { describe, expect, it } from "vitest";
import { ApiError, parseEvent, type RunEvent } from "../lib/api";
import { CueNumbers, elapsedSeconds, initialRun, runReducer, totalsByModel, type RunState } from "../lib/run-state";
import { megabytes, modelName, noteText, roleName, stageDetail, usd } from "../lib/run-format";

const ev = (raw: Record<string, unknown>): RunEvent => {
  const e = parseEvent(JSON.stringify(raw));
  if (!e) throw new Error(`bad event ${JSON.stringify(raw)}`);
  return e;
};

function play(events: Record<string, unknown>[], from: RunState = initialRun(), at = 0): RunState {
  return events.reduce<RunState>((s, raw) => runReducer(s, { type: "event", ev: ev(raw), at }), from);
}

const RUN = [
  { type: "run", run_id: "live-1", duration: 90, options: { mode: "standard", voice: "Sofia" }, seq: 0, ts: 100 },
  { type: "stage_start", stage: "listen", seq: 1, ts: 100.1 },
  { type: "stage_end", stage: "listen", seconds: 3.2, cached: false, words: 212, windows: 6, capacity_words: 74, speech_s: 41.2, has_audio: true, seq: 2, ts: 103.3 },
  { type: "stage_start", stage: "watch", seq: 3, ts: 103.4 },
  { type: "call", stage: "watch", role: "watch", model: "nvidia/Nemotron-3.5-Super-VL", tokens_in: 1000, tokens_out: 100, cost_usd: 0.0004, latency_s: 2, seq: 4, ts: 105 },
  { type: "call", stage: "watch", role: "watch", model: "nvidia/Nemotron-3.5-Super-VL", tokens_in: 2000, tokens_out: 200, cost_usd: 0.0008, latency_s: 3, seq: 5, ts: 108 },
  { type: "call", stage: "plan", role: "plan", model: "nvidia/Nemotron-3-Ultra-550b-a55b", tokens_in: 5000, tokens_out: 900, cost_usd: 0.0077, latency_s: 9, seq: 6, ts: 117 },
];

describe("runReducer", () => {
  it("follows a run from sending to done", () => {
    let s = runReducer(initialRun(), { type: "send", source: { kind: "sample", id: "ed90", title: "Elephants Dream" }, at: 5_000 });
    expect(s.phase).toBe("sending");
    s = play(RUN, s);
    expect(s.phase).toBe("running");
    expect([s.runId, s.duration, s.options?.voice]).toEqual(["live-1", 90, "Sofia"]);
    expect(s.stages.listen).toMatchObject({ status: "done", seconds: 3.2 });
    expect(s.stages.watch.status).toBe("active");
    expect(s.current).toBe("watch");
    expect(s.heard).toEqual({ words: 212, pauses: 6, capacityWords: 74, speechSeconds: 41.2, hasAudio: true });
    expect(s.calls).toHaveLength(3);
    expect(s.cost).toBeCloseTo(0.0089);
    expect([s.tokensIn, s.tokensOut]).toEqual([8000, 1200]);

    s = play(
      [
        { type: "cue", id: "c003", status: "verified", text: "B", start: 30, audio_dur: 2, note: null, seq: 7, ts: 120 },
        { type: "cue", id: "c001", status: "held", text: "A", start: 10, audio_dur: null, note: "not visible", seq: 8, ts: 121 },
        { type: "cue", id: "x004", status: "extended", text: "X", start: 20, audio_dur: 1.5, note: null, seq: 9, ts: 122 },
        { type: "done", run_id: "live-1", summary: { cues: { verified: 1, held: 1, extended: 1 } }, seq: 10, ts: 130 },
      ],
      s,
    );
    expect(s.phase).toBe("done");
    // shown in time order, numbered in the order they were finalised, extended counted apart
    expect(s.cues.map((c) => [c.id, c.n, c.extended])).toEqual([
      ["c001", 2, false],
      ["x004", 1, true],
      ["c003", 1, false],
    ]);
    expect(s.latestCue).toBe("x004");
    expect(s.summary?.cues).toEqual({ verified: 1, held: 1, extended: 1 });
    expect(elapsedSeconds(s, 999_999_999)).toBeCloseTo(30);
  });

  it("ignores repeats by seq", () => {
    const once = play(RUN);
    const twice = play(RUN, once);
    expect(twice).toBe(once);
  });

  it("marks the stage it failed in and keeps the reason", () => {
    const s = play([...RUN, { type: "error", kind: "BudgetExceeded", message: "The live demo has used its model budget for today.", scope: "day", stage: "plan", seq: 7 }]);
    expect(s.phase).toBe("error");
    expect(s.stages.watch.status).toBe("active"); // plan never started in this script
    expect(s.error).toEqual({ kind: "BudgetExceeded", message: "The live demo has used its model budget for today.", scope: "day", stage: "plan" });

    const t = play([...RUN, { type: "error", kind: "ValueError", message: "boom", seq: 7 }]);
    expect(t.stages.watch.status).toBe("failed");
  });

  it("runs the stages again on a resumed attempt, keeping cues and calls", () => {
    const s = play([...RUN, { type: "cue", id: "c001", status: "verified", text: "A", start: 1, audio_dur: 1, note: null, seq: 7 }]);
    const r = play([{ type: "run", run_id: "live-1", duration: 90, resumed: true, seq: 8 }], s);
    expect(r.resumed).toBe(true);
    expect(r.stages.listen.status).toBe("waiting");
    expect(r.cues).toHaveLength(1);
    expect(r.calls).toHaveLength(3);
  });

  it("keeps what it saw when following the same run again, and starts fresh for another", () => {
    const s = play(RUN);
    const lost = runReducer(s, { type: "lost", error: null });
    expect(lost.phase).toBe("lost");
    const again = runReducer(lost, { type: "follow", runId: "live-1", at: 0 });
    expect([again.phase, again.calls.length, again.lastSeq]).toEqual(["running", 3, 6]);
    const other = runReducer(lost, { type: "follow", runId: "live-9", at: 0 });
    expect([other.runId, other.calls.length, other.source]).toEqual(["live-9", 0, { kind: "follow", id: "live-9" }]);
  });

  it("goes back to choosing on a refusal, keeping it to show", () => {
    const sending = runReducer(initialRun(), { type: "send", source: { kind: "file", name: "a.mp4", bytes: 10 }, at: 0 });
    const s = runReducer(sending, { type: "refused", error: new ApiError({ status: 409, code: "busy", retryAfter: 60 }) });
    expect([s.phase, s.refusal?.code, s.refusal?.retryAfter, s.source?.kind]).toEqual(["idle", "busy", 60, "file"]);
  });

  it("measures elapsed time on the server's clock while running", () => {
    // events stamped 100..117 s arrived when the client clock read 1000.5 s at the latest
    const s = play(RUN, initialRun(), 1_000_500);
    // client - server offset is 1000.5 - 117 = 883.5 at best; five client seconds later:
    expect(elapsedSeconds(s, 1_005_500)).toBeCloseTo(1005.5 - 883.5 - 100);
  });
});

describe("numbers, totals and words", () => {
  it("numbers cues within their kind and keeps a number once given", () => {
    const n = new CueNumbers();
    expect(n.label({ id: "c001", status: "verified" })).toEqual({ n: 1, extended: false });
    expect(n.label({ id: "x002", status: "held" })).toEqual({ n: 1, extended: true });
    expect(n.label({ id: "c003", status: "held" })).toEqual({ n: 2, extended: false });
    expect(n.label({ id: "c001", status: "held" })).toEqual({ n: 1, extended: false });
  });

  it("groups calls by model with the roles each played", () => {
    const s = play(RUN);
    expect(totalsByModel(s.calls).map((t) => [modelName(t.model), t.calls, t.roles.map(roleName)])).toEqual([
      ["Nemotron 3.5 Super VL", 2, ["Watch"]],
      ["Nemotron 3 Ultra", 1, ["Plan"]],
    ]);
  });

  it("says what each stage reported", () => {
    expect(stageDetail("listen", { words: 1, windows: 6 })).toBe("1 word, 6 pauses");
    expect(stageDetail("research", { skipped: true })).toBe("Skipped");
    expect(stageDetail("schedule", { placed: 5, extended: ["e1"] })).toBe("5 placed, 1 for extended");
    expect(stageDetail("finalize", { verified: 5, held: 0 })).toBe("5 verified");
    expect(stageDetail("watch", {})).toBeNull();
    expect(modelName("nvidia/Some-New_Model")).toBe("Some New Model");
    expect(usd(0.00041)).toBe("$0.0004");
    expect(usd(1.234)).toBe("$1.23");
    expect([megabytes(30 * 2 ** 20), megabytes(13_000_000)]).toEqual(["30 MB", "12.4 MB"]);
    expect(noteText("undescribed", "2 salient events have no description from the plan and stay undescribed: e07, e09", 2)).toBe(
      "2 key moments stay undescribed: the plan drafted no line for them.",
    );
    expect(noteText("research_error", "research: timeout", null)).toBe("research: timeout");
  });
});
