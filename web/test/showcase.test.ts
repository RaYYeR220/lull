import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { parseIndex, parseShowcase, requiredFiles } from "../lib/showcase-schema.mjs";
import {
  closeUp,
  linesInsidePauses,
  numberedCues,
  spend,
  speechOverlap,
  spokenFromDraft,
  verifiedCue,
  voicedCues,
} from "../lib/showcase-derive";

const ROOT = path.join(__dirname, "..", "public", "showcase");
const raw = JSON.parse(readFileSync(path.join(ROOT, "ed90", "showcase.json"), "utf8")) as Record<string, unknown>;
const show = parseShowcase(raw, "ed90");
const clone = () => structuredClone(raw) as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

describe("the shipped showcase is the engine's real export", () => {
  it("is not an interim placeholder", () => {
    expect(raw.interim).toBeUndefined();
    expect(show.interim).toBeUndefined();
  });

  it("reads words as {t, s, e} and every path as a URL under /showcase/<id>/", () => {
    expect(show.words?.[0]).toEqual({ text: "Safe", start: 0.24, end: 0.8 });
    expect(show.audio).toEqual({ original: "/showcase/ed90/video.mp4", described: "/showcase/ed90/described-audio.m4a" });
    expect(show.cueAudioBase).toBe("/showcase/ed90/audio/");
    expect(show.verifyExample?.cueId).toBe("c002");
  });

  it("lists in the index exactly as its showcase.json reads", () => {
    const index = parseIndex(JSON.parse(readFileSync(path.join(ROOT, "index.json"), "utf8")));
    expect(index).toEqual([{ id: "ed90", title: show.title, credit: show.credit, duration: show.duration, cues: show.cues.length, costUsd: show.costUsd }]);
  });

  it("names every file the player needs", () => {
    const files = requiredFiles(raw);
    expect(files).toEqual(expect.arrayContaining(["showcase.json", "video.mp4", "described-audio.m4a", "descriptions.vtt", "extended.vtt", "audio/c001.m4a", "audio/x002.m4a"]));
    expect(files.filter((f) => f.startsWith("audio/"))).toHaveLength(show.cues.length);
  });
});

describe("what the pages derive from the run", () => {
  it("voices standard lines only, none over speech, each inside its pause", () => {
    const voiced = voicedCues(show);
    expect(voiced.map((c) => c.id)).toEqual(["c001", "c002", "c005", "c007", "c008", "c011", "c012", "c013"]);
    expect(voiced.every((c) => c.status !== "held" && c.status !== "extended")).toBe(true);
    expect(speechOverlap(show)).toEqual({ cues: 0, seconds: 0 });
    expect(linesInsidePauses(show)).toEqual({ inside: 8, of: 8 });
  });

  it("numbers standard and extended lines apart, in time order", () => {
    const labels = numberedCues(show).map((c) => `${c.id}=${c.label}`);
    expect(labels).toEqual([
      "x001=Extended 1",
      "x002=Extended 2",
      "c001=Cue 1",
      "c002=Cue 2",
      "c005=Cue 3",
      "c007=Cue 4",
      "c008=Cue 5",
      "c011=Cue 6",
      "c012=Cue 7",
      "c013=Cue 8",
    ]);
  });

  it("finds the verify example's cue by its id, and the spoken line is that cue's text", () => {
    const cue = verifiedCue(show);
    expect(cue?.id).toBe("c002");
    expect(cue?.label).toBe("Cue 2");
    expect(cue?.text).toBe("The two people are surrounded by wires.");
    // cutting the claim the frames didn't show leaves the draft's own words
    expect(show.verifyExample && spokenFromDraft(show.verifyExample)).toBe("The two people surrounded by wires.");
  });

  it("falls back to the claims when the export names no cue", () => {
    const r = clone();
    delete r.verifyExample.cueId;
    expect(verifiedCue(parseShowcase(r, "ed90"))?.id).toBe("c002");
  });

  it("closes up on a pause one guard from words on both sides", () => {
    const cu = closeUp(show);
    expect(cu?.gap.id).toBe("g001");
    expect(cu?.cue.id).toBe("c001");
    expect([cu?.a, cu?.b]).toEqual([7.5, 17]);
    expect(cu?.guardBefore).toBe(true);
    expect(cu?.guardAfter).toBe(true);
    expect(cu?.before.map((w) => w.text).join(" ")).toBe("You'll see");
    expect(cu?.after.map((w) => w.text).join(" ")).toBe("Emo");
    expect(cu?.prevSpeechEnd).toBe(8.8);
    expect(cu?.nextSpeechStart).toBe(15.741);
    // speech as heard, clipped to the close-up
    expect(cu?.spans).toEqual([
      { start: 7.5, end: 8.8 },
      { start: 15.741, end: 17 },
    ]);
  });

  it("draws no guard where a pause doesn't sit one guard from speech", () => {
    // only Cue 3 left: its pause opens a guard after speech but is cut short by the next
    // window, with the next speech 15 s away
    const r = clone();
    r.cues = (r.cues as { id: string }[]).filter((c) => c.id === "c005");
    delete r.verifyExample;
    const cu = closeUp(parseShowcase(r, "ed90"));
    expect(cu?.gap.id).toBe("g005");
    expect(cu?.guardBefore).toBe(true);
    expect(cu?.guardAfter).toBe(false);
    expect(cu?.after).toEqual([]);
  });

  it("says the run's spend as a figure and in words", () => {
    expect(spend(show.costUsd ?? 0)).toEqual({ figure: "$0.04", words: "About four cents" });
    expect(spend(0.004)).toEqual({ figure: "<$0.01", words: "Under a cent" });
  });
});

describe("a showcase.json that breaks the schema is refused with the field at fault", () => {
  const refuse = (mutate: (r: Record<string, any>) => void, why: RegExp, id = "ed90") => { // eslint-disable-line @typescript-eslint/no-explicit-any
    const r = clone();
    mutate(r);
    expect(() => parseShowcase(r, id)).toThrow(why);
  };

  it("refuses absolute paths", () => {
    refuse((r) => (r.audio.original = "C:/Users/someone/lull-runs/ed90/video.mp4"), /audio\.original is an absolute path/);
    refuse((r) => (r.audio.described = "/home/someone/described-audio.m4a"), /audio\.described is an absolute path/);
    refuse((r) => (r.audio.original = "\\\\server\\share\\video.mp4"), /absolute path/);
    refuse((r) => (r.audio.original = "https://elsewhere.example/a.m4a"), /absolute path/);
    refuse((r) => (r.cueAudioBase = "/audio/"), /cueAudioBase is an absolute path/);
    refuse((r) => (r.cueAudioBase = "D:\\export\\audio\\"), /cueAudioBase is an absolute path/);
  });

  it("refuses paths that leave the folder or aren't plain", () => {
    refuse((r) => (r.audio.original = "../ed90.mp4"), /audio\.original must be a plain path/);
    refuse((r) => (r.audio.original = "audio/../../x.mp4"), /audio\.original must be a plain path/);
    refuse((r) => (r.audio.original = ".hidden.mp4"), /plain path/);
    refuse((r) => (r.cueAudioBase = "audio"), /cueAudioBase must be a plain path inside the showcase folder, ending in \//);
  });

  it("refuses bad fields", () => {
    refuse((r) => (r.cues[0].status = "maybe"), /cues\[0\]\.status/);
    refuse((r) => (r.cues[1].id = r.cues[0].id), /cues\[1\]\.id .* twice/);
    refuse((r) => (r.cues[0].id = "../x"), /cues\[0\]\.id/);
    refuse((r) => (r.words[0] = { text: "Safe", start: 0.24, end: 0.8 }), /words\[0\] must be \{ t, s <= e \}/);
    refuse((r) => (r.verifyExample.cueId = "c999"), /verifyExample\.cueId/);
    refuse((r) => (r.verifyExample.claims[0].verdict = "maybe"), /verifyExample\.claims\[0\]/);
    refuse((r) => (r.costUsd = -1), /costUsd/);
    refuse((r) => (r.gaps[0].end = r.gaps[0].start), /gaps\[0\]/);
    refuse((r) => (r.id = "other"), /id is other, expected ed90/);
    refuse(() => {}, /id must be lowercase/, "ED90");
  });
});
