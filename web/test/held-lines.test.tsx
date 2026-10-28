import { readFileSync } from "node:fs";
import path from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RunChecks } from "../components/watch/RunChecks";
import { showcaseReport } from "../lib/report-data";
import { fromShowcase, runLines } from "../lib/run-data";
import { lineCounts } from "../lib/showcase-derive";
import { indexEntry, parseShowcase, requiredFiles } from "../lib/showcase-schema.mjs";

/** The shipped export: it lists its one held line (c002), with the reason, and its summary counts it. */
const RAW = JSON.parse(readFileSync(path.join(__dirname, "..", "public", "showcase", "ed90", "showcase.json"), "utf8")) as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
const clone = () => structuredClone(RAW);

/** An export from before held lines were listed: the summary counts them, the cues leave them out. */
function unlisted() {
  const r = clone();
  r.cues = r.cues.filter((c: { status: string }) => c.status !== "held");
  return r;
}

describe("held lines in a recorded run", () => {
  it("are read with their reason, and need no recording", () => {
    const s = parseShowcase(RAW, "ed90");
    const c002 = s.cues.find((c) => c.id === "c002")!;
    expect(c002).toMatchObject({ status: "held", text: "The younger person runs across a narrow metal walkway in a dark wire-filled space." });
    expect(c002.note).toMatch(/^does not fit: 5\.85 s voiced for a 5\.14 s pause/);
    expect(requiredFiles(RAW)).not.toContain("audio/c002.m4a");
    const noReason = clone();
    delete noReason.cues.find((c: { id: string }) => c.id === "c002").note;
    expect(parseShowcase(noReason, "ed90").cues.find((c) => c.id === "c002")!.note).toBeUndefined();
    expect(() => parseShowcase({ ...RAW, cues: [{ ...RAW.cues[0], note: 3 }] }, "ed90")).toThrow(/cues\[0\]\.note/);
  });

  it("are counted from the export's summary while the export doesn't list them", () => {
    expect(lineCounts(parseShowcase(unlisted(), "ed90"))).toEqual({ passed: 10, held: 1, heldListed: 0, extended: 2, reviewed: 0 });
  });

  it("are counted once the export lists them, the summary agreeing", () => {
    expect(lineCounts(parseShowcase(RAW, "ed90"))).toEqual({ passed: 10, held: 1, heldListed: 1, extended: 2, reviewed: 0 });
  });

  it("are never guessed from the published lines alone: without a summary or listed held lines, unknown", () => {
    const { summary: _s, ...bare } = unlisted(); // eslint-disable-line @typescript-eslint/no-unused-vars
    expect(lineCounts(parseShowcase(bare, "ed90")).held).toBeNull();
  });

  it("show in the run's script with their reason, and are not voiced", () => {
    const run = fromShowcase(parseShowcase(RAW, "ed90"));
    const held = run.cues.filter((c) => c.status === "held");
    expect(held.map((c) => c.id)).toEqual(["c002"]);
    expect(held[0]).toMatchObject({ note: expect.stringMatching(/^does not fit/), audioDur: null, kind: "standard" });
    expect(runLines(run)).toEqual({ passed: 10, held: 1, heldListed: 1, extended: 2, reviewed: 0 });
  });

  it("the watch page's checks and the report give the same counts", () => {
    const s = parseShowcase(RAW, "ed90");
    const run = fromShowcase(s);
    const html = renderToStaticMarkup(<RunChecks run={run} />);
    // every line goes through the frame check, the two extended ones too: 12 of 13 passed
    expect(html).toContain("12 of 13");
    expect(html).toContain("1 held for review");
    expect(html).not.toContain("13 of 13");
    const r = showcaseReport(s, run);
    expect((r.counts.verified ?? 0) + (r.counts.edited ?? 0) + (r.counts.approved ?? 0)).toBe(10);
    expect(r.counts.extended).toBe(2);
    expect(r.counts.held).toBe(1);
    // nobody reviewed these lines, and the page says so plainly
    expect(html).toContain("Not yet");
    expect(html).toContain("Needs review");
  });

  it("a held extended line is counted held, not extended", () => {
    const tos = JSON.parse(readFileSync(path.join(__dirname, "..", "public", "showcase", "tos180", "showcase.json"), "utf8"));
    const s = parseShowcase(tos, "tos180");
    expect(s.cues.filter((c) => c.status === "held" && c.extended)).not.toHaveLength(0);
    expect(lineCounts(s)).toMatchObject({ passed: 9, held: 6, heldListed: 6, extended: 14 });
    const html = renderToStaticMarkup(<RunChecks run={fromShowcase(s)} />);
    expect(html).toContain("23 of 29");
    expect(html).toContain("14 lines");
  });

  it("the index row counts them too", () => {
    expect(indexEntry(parseShowcase(RAW, "ed90"))).toMatchObject({ cues: 13, held: 1 });
  });
});

describe("what the landing says about review", () => {
  it("never calls a run approved that a person hasn't signed off", async () => {
    const { reviewState } = await import("../components/landing/Outputs");
    expect(reviewState(parseShowcase(RAW, "ed90"))).toBe("Needs review in the studio: 1 held, none approved yet");
    const r = clone();
    r.summary.counts = { verified: 0, edited: 3, approved: 7, held: 0, extended: 0 };
    r.cues = r.cues.map((c: { status: string }) => ({ ...c, status: "approved" }));
    expect(reviewState(parseShowcase(r, "ed90"))).toBe("Reviewed and approved in the studio");
  });
});
