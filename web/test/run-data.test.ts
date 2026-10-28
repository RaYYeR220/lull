import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { API_BASE } from "../lib/api";
import { doneOrThrow, readDetail, type DoneDetail } from "../lib/api-runs";
import { borrowedWords, clipOf, fitWords, fromApi, fromShowcase, voicedLines } from "../lib/run-data";
import { parseShowcase } from "../lib/showcase";
import { countWords } from "../lib/studio-budget";

const raw = JSON.parse(readFileSync(path.join(__dirname, "..", "public", "showcase", "ed90", "showcase.json"), "utf8"));
const show = parseShowcase(raw, "ed90");

/** A done run's detail as the API gives it; `speech`, `gaps`, `loudness`, `original` as given (absent: null). */
function detail(
  over: {
    id?: string;
    kind?: "showcase" | "live";
    name?: string;
    duration?: number;
    outputs?: string[];
    cues?: unknown[];
    extended?: unknown[];
    source?: unknown;
    speech?: unknown;
    gaps?: unknown;
    loudness?: unknown;
    original?: string | null;
  } = {},
): DoneDetail {
  const outputs = Object.fromEntries((over.outputs ?? ["descriptions.vtt", "extended.vtt", "described-audio.m4a", "described.mp4", "cues.json"]).map((n) => [n, { bytes: 100 }]));
  return doneOrThrow(
    readDetail({
    id: over.id ?? "r1",
    kind: over.kind ?? "live",
    status: "done",
    title: over.name ?? "clip.mp4",
    duration: over.duration ?? 30,
    speech: over.speech ?? null,
    gaps: over.gaps ?? null,
    loudness: over.loudness ?? null,
    original: over.original ?? null,
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
    ...(over.source !== undefined ? { source: over.source } : {}),
  })!,
  );
}

const INDEX = [{ id: "ed90", title: "Elephants Dream", credit: "c", duration: 90, cues: 10 }];

describe("a recorded run", () => {
  const run = fromShowcase(show);

  it("numbers lines in time order, standard and extended apart, each in its pause", () => {
    expect(run.cues.map((c) => c.label)).toEqual(["Extended 1", "Extended 2", "Cue 1", "Cue 2", "Cue 3", "Cue 4", "Cue 5", "Cue 6", "Cue 7", "Cue 8"]);
    const first = run.cues.find((c) => c.id === "c001")!;
    expect(first.gap).toEqual({ id: "g001", start: 9, end: 15.64, capacity: 20 });
    expect(first.frame).toBe("/showcase/ed90/keyframes/c001.jpg");
    expect(voicedLines(run)).toHaveLength(8);
  });

  it("keeps the verify example's verdicts on its own line, marked as the draft's", () => {
    const c013 = run.cues.find((c) => c.id === "c013")!;
    expect(c013.claimsOf).toBe("draft");
    expect(c013.claims?.map((k) => k.verdict)).toEqual(["not_visible", "supported"]);
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
  it("without its original video: described.mp4 with its sound marked, the API's files, provenance by role", () => {
    const run = fromApi("r1", detail({ speech: [[0, 1.5]], gaps: [{ id: "g1", start: 1.8, end: 6, capacity: 12 }] }));
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

  it("plays the original the API serves, with the descriptions over it, and its own timeline, rate and loudness", () => {
    const run = fromApi("r1", detail({ original: "/v1/runs/r1/original", speech: [[0, 1.5]], gaps: [{ id: "g1", start: 1.8, end: 6, capacity: 12 }], loudness: [-30, -31.5] }));
    expect(run.media).toMatchObject({ src: `${API_BASE}/v1/runs/r1/original`, describedAudio: `${API_BASE}/v1/runs/r1/files/described-audio.m4a` });
    expect(run.media?.sound).toBeUndefined();
    expect(run.loudness).toEqual([-30, -31.5]);
    expect(run.speech).toEqual([[0, 1.5]]);
    expect(run.borrowed).toBeNull();
  });

  it("orders and names lines, ends a voiced one at its take and leaves a held one open", () => {
    const run = fromApi("r1", detail({ gaps: [{ id: "g1", start: 1.8, end: 6, capacity: 12 }] }));
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

  it("borrows from a recorded run only when the API says the run is that recorded run, a copy of it, or a run of its sample", () => {
    expect(clipOf(detail({ id: "ed90", kind: "showcase", source: { kind: "sample", sample_id: null } }), INDEX)?.id).toBe("ed90");
    expect(clipOf(detail({ source: { kind: "fork", forked_from: "ed90", origin: "sample" } }), INDEX)?.id).toBe("ed90");
    expect(clipOf(detail({ name: "x.mp4", source: { kind: "sample", sample_id: "ed90" } }), INDEX)?.id).toBe("ed90");
    // never by the file's name: an upload called ed90.mp4 is an upload
    expect(clipOf(detail({ name: "ed90.mp4", duration: 90, source: { kind: "upload" } }), INDEX)).toBeNull();
    expect(clipOf(detail({ name: "ed90.mp4", duration: 90 }), INDEX)).toBeNull();
    expect(clipOf(detail({ source: { kind: "sample", sample_id: "ed45" } }), INDEX)).toBeNull();
    expect(clipOf(detail({ source: { kind: "fork", forked_from: "live-20261027-214500-a1b2c3", origin: "sample" } }), INDEX)).toBeNull();
  });

  it("a copy of the recorded run lends only what the API has no copy of: its soundtrack levels", () => {
    const d = detail({
      name: "ed90.mp4",
      duration: 90,
      source: { kind: "fork", forked_from: "ed90", origin: "sample" },
      original: "/v1/runs/r1/original",
      speech: [[0, 1]],
      gaps: [{ id: "g1", start: 1.8, end: 6, capacity: 12 }],
    });
    const lent = fromApi("r1", d, { clip: show });
    expect(lent.title).toBe("Elephants Dream");
    expect(lent.loudness).toHaveLength(900);
    expect(lent.gaps).toEqual([{ id: "g1", start: 1.8, end: 6, capacity: 12 }]);
    expect(lent.borrowed).toEqual({ from: "ed90", title: "Elephants Dream", loudness: true, speech: false, gaps: false });
    // the API's own copy of the video
    expect(lent.media?.src).toBe(`${API_BASE}/v1/runs/r1/original`);

    // with loudness of its own, nothing is lent
    expect(fromApi("r1", detail({ ...{ source: { kind: "fork", forked_from: "ed90", origin: "sample" } }, speech: [[0, 1]], gaps: [{ id: "g1", start: 1.8, end: 6, capacity: 12 }], loudness: [-30] }), { clip: show }).borrowed).toBeNull();

    // an older copy without speech or pauses of its own borrows those too, and the recorded video
    const older = fromApi("r1", detail({ name: "ed90.mp4", duration: 90, source: { kind: "fork", forked_from: "ed90", origin: "sample" } }), { clip: show });
    expect(older.borrowed).toEqual({ from: "ed90", title: "Elephants Dream", loudness: true, speech: true, gaps: true });
    expect(older.gaps).toHaveLength(13);
    expect(older.media?.src).toBe("/showcase/ed90/video.mp4");
  });

  it("says exactly which pieces were lent", () => {
    const b = { from: "ed90", title: "Elephants Dream" };
    expect(borrowedWords({ ...b, loudness: true, speech: false, gaps: false })).toBe("The soundtrack levels are from the recorded run of this clip, Elephants Dream.");
    expect(borrowedWords({ ...b, loudness: true, speech: true, gaps: false })).toBe(
      "The soundtrack levels and speech are from the recorded run of this clip, Elephants Dream; this run’s own files don’t include its speech.",
    );
    expect(borrowedWords({ ...b, loudness: true, speech: true, gaps: true })).toBe(
      "The soundtrack levels, speech and pauses are from the recorded run of this clip, Elephants Dream; this run’s own files don’t include its speech or pauses.",
    );
    expect(borrowedWords({ ...b, loudness: true, speech: false, gaps: true })).toBe(
      "The soundtrack levels and pauses are from the recorded run of this clip, Elephants Dream; this run’s own files don’t include its pauses.",
    );
  });

  it("a line's fit names a borrowed pause as the recorded run's", () => {
    const d = detail({ name: "ed90.mp4", duration: 90, source: { kind: "fork", forked_from: "ed90", origin: "sample" }, cues: [{ id: "c001", gap_id: "g001", start: 9, text: "Two people walk on.", event_ids: [], status: "verified", claims: [], audio_dur: 2 }], extended: [] });
    const lent = fromApi("r1", d, { clip: show });
    expect(fitWords(lent.cues[0]!, countWords)).toBe("4 of 20 words");
    expect(fitWords(lent.cues[0]!, countWords, { borrowed: true })).toBe("4 of 20 words, in the recorded run’s pause");
  });

  it("the recorded run opened through the API keeps the site's media and files", () => {
    const run = fromApi("ed90", detail({ id: "ed90", kind: "showcase", name: "ed90.mp4", duration: 90 }), { clip: show, clipFiles: fromShowcase(show).files });
    expect(run.loudness).toHaveLength(900);
    expect(run.media?.src).toBe("/showcase/ed90/video.mp4");
    expect(run.files[0]?.href).toBe("/showcase/ed90/descriptions.vtt");
    expect(run.borrowed).toBeNull();
  });

  it("without a video among its files, there is nothing for the player", () => {
    expect(fromApi("r1", detail({ outputs: ["descriptions.vtt"] })).media).toBeNull();
  });
});
