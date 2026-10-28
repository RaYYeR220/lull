import { readFileSync } from "node:fs";
import path from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RunChecks } from "../components/watch/RunChecks";
import { showcaseReport } from "../lib/report-data";
import { fromShowcase, runLines } from "../lib/run-data";
import { lineCounts } from "../lib/showcase-derive";
import { indexEntry, parseShowcase, requiredFiles } from "../lib/showcase-schema.mjs";

/** The shipped export: its summary counts 4 held lines that it doesn't list. */
const RAW = JSON.parse(readFileSync(path.join(__dirname, "..", "public", "showcase", "ed90", "showcase.json"), "utf8")) as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
const clone = () => structuredClone(RAW);

/** The shape the engine's re-export will have: held lines listed, with their reason, not voiced. */
function withHeld() {
  const r = clone();
  r.cues.push(
    { id: "c003", start: 23.694, end: 23.694, text: "Two figures dangle from the wires.", status: "held", ahead: false, note: "says two figures; the frames show one" },
    { id: "c004", start: 29.137, end: 29.137, text: "The walkway sways.", status: "held", ahead: false, note: null },
    { id: "c009", start: 64.339, end: 64.339, text: "Proog points up.", status: "held", ahead: false },
    { id: "c012", start: 78.12, end: 78.12, text: "Emo looks down at the gap.", status: "held", ahead: false, note: "held by the verifier: no gap is visible" },
  );
  return r;
}

describe("held lines in a recorded run", () => {
  it("are read with their reason, and need no recording", () => {
    const s = parseShowcase(withHeld(), "ed90");
    const c003 = s.cues.find((c) => c.id === "c003")!;
    expect(c003).toMatchObject({ status: "held", note: "says two figures; the frames show one" });
    expect(s.cues.find((c) => c.id === "c009")!.note).toBeUndefined();
    expect(requiredFiles(withHeld())).not.toContain("audio/c003.m4a");
    expect(() => parseShowcase({ ...withHeld(), cues: [{ ...withHeld().cues[0], note: 3 }] }, "ed90")).toThrow(/cues\[0\]\.note/);
  });

  it("are counted from the export's summary while the export doesn't list them", () => {
    expect(lineCounts(parseShowcase(RAW, "ed90"))).toEqual({ passed: 8, held: 4, heldListed: 0, extended: 2, reviewed: 0 });
  });

  it("are counted once the export lists them, the summary agreeing", () => {
    expect(lineCounts(parseShowcase(withHeld(), "ed90"))).toEqual({ passed: 8, held: 4, heldListed: 4, extended: 2, reviewed: 0 });
  });

  it("are never guessed from the published lines alone: without a summary or listed held lines, unknown", () => {
    const { summary: _s, ...bare } = clone(); // eslint-disable-line @typescript-eslint/no-unused-vars
    expect(lineCounts(parseShowcase(bare, "ed90")).held).toBeNull();
  });

  it("show in the run's script with their reason, and are not voiced", () => {
    const run = fromShowcase(parseShowcase(withHeld(), "ed90"));
    const held = run.cues.filter((c) => c.status === "held");
    expect(held.map((c) => c.id)).toEqual(["c003", "c004", "c009", "c012"]);
    expect(held[0]).toMatchObject({ note: "says two figures; the frames show one", audioDur: null, kind: "standard" });
    expect(runLines(run)).toEqual({ passed: 8, held: 4, heldListed: 4, extended: 2, reviewed: 0 });
  });

  it("the watch page's checks and the report give the same counts", () => {
    const s = parseShowcase(RAW, "ed90");
    const run = fromShowcase(s);
    const html = renderToStaticMarkup(<RunChecks run={run} />);
    expect(html).toContain("8 of 12");
    expect(html).toContain("4 held for review");
    expect(html).not.toContain("10 of 10");
    const r = showcaseReport(s, run);
    expect((r.counts.verified ?? 0) + (r.counts.edited ?? 0) + (r.counts.approved ?? 0)).toBe(8);
    expect(r.counts.held).toBe(4);
    // nobody reviewed these lines, and the page says so plainly
    expect(html).toContain("Not yet");
    expect(html).toContain("Needs review");
  });

  it("the index row counts them too", () => {
    expect(indexEntry(parseShowcase(RAW, "ed90"))).toMatchObject({ cues: 10, held: 4 });
  });
});

describe("what the landing says about review", () => {
  it("never calls a run approved that a person hasn't signed off", async () => {
    const { reviewState } = await import("../components/landing/Outputs");
    expect(reviewState(parseShowcase(RAW, "ed90"))).toBe("Needs review in the studio: 4 held, none approved yet");
    const r = clone();
    r.summary.counts = { verified: 0, edited: 3, approved: 7, held: 0, extended: 0 };
    r.cues = r.cues.map((c: { status: string }) => ({ ...c, status: "approved" }));
    expect(reviewState(parseShowcase(r, "ed90"))).toBe("Reviewed and approved in the studio");
  });
});
