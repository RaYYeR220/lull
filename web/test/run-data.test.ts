import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { API_BASE } from "../lib/api";
import { readDetail, type RunDetail } from "../lib/api-runs";
import { fitWords, fromApi, fromShowcase, sameClip, voicedLines } from "../lib/run-data";
import { parseShowcase } from "../lib/showcase";
import { countWords } from "../lib/studio-budget";

const raw = JSON.parse(readFileSync(path.join(__dirname, "..", "public", "showcase", "ed90", "showcase.json"), "utf8"));
const show = parseShowcase(raw, "ed90");

function detail(over: { name?: string; duration?: number; outputs?: string[]; cues?: unknown[]; extended?: unknown[] } = {}): RunDetail {
  const outputs = Object.fromEntries((over.outputs ?? ["descriptions.vtt", "extended.vtt", "described-audio.m4a", "described.mp4", "cues.json"]).map((n) => [n, { bytes: 100 }]));
  return readDetail({
    manifest: {
      run_id: "r1",
      created: "2026-10-27T21:46:38+00:00",
      video: { name: over.name ?? "clip.mp4", duration: over.duration ?? 30 },
      options: { voice: "Sofia" },
      outputs,
      summary: {
        cost_usd: 0.02,
        calls_by_role_model: [
          { role: "watch", model: "nvidia/Nemotron-3.5-Super-VL", calls: 3 },
          { role: "plan", model: "nvidia/Nemotron-3-Ultra-550b-a55b", calls: 1 },
          { role: "verify", model: "nvidia/Nemotron-3.5-Super-VL", calls: 4 },
          { role: "verify", model: "other", calls: 1 },
        ],
      },
      versions: { tts: "Magpie TTS Multilingual" },
    },
    cues: {
      cues: over.cues ?? [
        { id: "c002", gap_id: "g2", start: 12, text: "Two people walk on.", event_ids: [], status: "held", claims: [], audio_dur: null, note: "not seen" },
        { id: "c001", gap_id: "g1", start: 2, text: "A man waves at the camera now.", event_ids: [], status: "verified", claims: [], audio_dur: 2.5 },
      ],
      extended: over.extended ?? [{ id: "x001", gap_id: null, start: 0, text: "A room.", event_ids: [], status: "approved", claims: [], audio_dur: 1.2 }],
    },
    dirty: false,
  })!;
}

describe("a recorded run", () => {
  const run = fromShowcase(show);

  it("numbers lines in time order, standard and extended apart, each in its pause", () => {
    expect(run.cues.map((c) => c.label)).toEqual(["Extended 1", "Extended 2", "Cue 1", "Cue 2", "Cue 3", "Cue 4", "Cue 5", "Cue 6", "Cue 7", "Cue 8"]);
    const first = run.cues.find((c) => c.id === "c001")!;
    expect(first.gap).toEqual({ id: "g001", start: 9, end: 15.54, capacity: 19 });
    expect(first.frame).toBe("/showcase/ed90/keyframes/c001.jpg");
    expect(voicedLines(run)).toHaveLength(8);
  });

  it("keeps the verify example's verdicts on its own line, marked as the draft's", () => {
    const c002 = run.cues.find((c) => c.id === "c002")!;
    expect(c002.claimsOf).toBe("draft");
    expect(c002.claims?.map((k) => k.verdict)).toEqual(["supported", "not_visible", "supported"]);
    expect(run.cues.find((c) => c.id === "c001")!.claims).toBeNull();
  });

  it("works the voice's rate back from the pauses (the engine counted them at 3.05 words a second)", () => {
    expect(run.wps).not.toBeNull();
    expect(run.wps!).toBeGreaterThan(2.95);
    expect(run.wps!).toBeLessThan(3.1);
  });

  it("plays the site's own files", () => {
    expect(run.media).toMatchObject({ src: "/showcase/ed90/video.mp4", cueAudioBase: "/showcase/ed90/audio/", cueAudioExt: "m4a" });
    expect(run.files.map((f) => f.name)).toEqual(["descriptions.vtt", "extended.vtt", "described-audio.m4a", "video.mp4"]);
    const without = fromShowcase(show, { hasFrame: () => false });
    expect(without.cues.every((c) => c.frame === null)).toBe(true);
  });
});

describe("a run from the API", () => {
  it("an uploaded clip: described.mp4 with its sound marked, the API's files, provenance by role", () => {
    const run = fromApi("r1", detail(), { timeline: { speech: [[0, 1.5]], gaps: [{ id: "g1", start: 1.8, end: 6, capacity: 12 }] } });
    expect(run.title).toBe("clip.mp4");
    expect(run.media).toMatchObject({
      src: `${API_BASE}/v1/runs/r1/files/described.mp4`,
      sound: "described",
      crossOrigin: "anonymous",
      descriptions: `${API_BASE}/v1/runs/r1/files/descriptions.vtt`,
      cueAudioBase: `${API_BASE}/v1/runs/r1/cues/{id}/audio`,
    });
    expect(run.files.map((f) => f.name)).toEqual(["descriptions.vtt", "extended.vtt", "described-audio.m4a", "described.mp4", "cues.json"]);
    expect(run.loudness).toBeNull();
    expect(run.borrowed).toBeNull();
    expect(run.provenance).toEqual({
      watch: "nvidia/Nemotron-3.5-Super-VL",
      plan: "nvidia/Nemotron-3-Ultra-550b-a55b",
      verify: "nvidia/Nemotron-3.5-Super-VL",
      research: null,
      voice: "Magpie TTS Multilingual",
    });
  });

  it("orders and names lines, ends a voiced one at its take and leaves a held one open", () => {
    const run = fromApi("r1", detail(), { timeline: { speech: null, gaps: [{ id: "g1", start: 1.8, end: 6, capacity: 12 }] } });
    expect(run.cues.map((c) => [c.id, c.label])).toEqual([
      ["x001", "Extended 1"],
      ["c001", "Cue 1"],
      ["c002", "Cue 2"],
    ]);
    const c001 = run.cues[1]!;
    expect(c001.end).toBe(4.5);
    expect(c001.gap?.id).toBe("g1");
    expect(fitWords(c001, countWords)).toBe("7 of 12 words");
    const held = run.cues[2]!;
    expect(held.end).toBeNull();
    expect(held.gap).toBeNull(); // its pause isn't in the timeline
    expect(fitWords(held, countWords)).toBe("4 words");
    // an extended line keeps its kind whatever its status
    expect(run.cues[0]!.kind).toBe("extended");
    expect(fitWords(run.cues[0]!, countWords)).toBe("video holds 1.20 s");
  });

  it("a sample's clip borrows the recorded run's soundtrack and video, and its timeline only when the run lacks one", () => {
    const d = detail({ name: "ed90.mp4", duration: 90 });
    const entry = sameClip(d, [{ id: "ed90", title: "Elephants Dream", credit: "c", duration: 90, cues: 10 }]);
    expect(entry?.id).toBe("ed90");
    expect(sameClip(detail({ name: "ed90.mp4", duration: 42 }), [{ id: "ed90", title: "E", credit: "c", duration: 90, cues: 10 }])).toBeNull();

    const lent = fromApi("r1", d, { clip: show, timeline: { speech: null, gaps: null } });
    expect(lent.title).toBe("Elephants Dream");
    expect(lent.loudness).toHaveLength(900);
    expect(lent.gaps).toHaveLength(13);
    expect(lent.borrowed).toEqual({ from: "ed90", title: "Elephants Dream", timeline: true });
    expect(lent.media?.src).toBe("/showcase/ed90/video.mp4");
    expect(lent.media?.sound).toBeUndefined();

    const own = fromApi("r1", d, { clip: show, timeline: { speech: [[0, 1]], gaps: [{ id: "g1", start: 1.8, end: 6, capacity: 12 }] } });
    expect(own.gaps).toEqual([{ id: "g1", start: 1.8, end: 6, capacity: 12 }]);
    expect(own.borrowed?.timeline).toBe(false);
  });

  it("the recorded run opened through the API keeps the site's media and files", () => {
    const run = fromApi("ed90", detail({ name: "ed90.mp4", duration: 90 }), { clip: show, clipFiles: fromShowcase(show).files });
    expect(run.media?.src).toBe("/showcase/ed90/video.mp4");
    expect(run.files[0]?.href).toBe("/showcase/ed90/descriptions.vtt");
    expect(run.borrowed).toBeNull();
  });

  it("without a video among its files, there is nothing for the player", () => {
    expect(fromApi("r1", detail({ outputs: ["descriptions.vtt"] })).media).toBeNull();
  });
});
