import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { readDetail } from "../lib/api-runs";
import { apiReport, showcaseReport } from "../lib/report-data";
import { fromApi, fromShowcase } from "../lib/run-data";
import { parseShowcase } from "../lib/showcase";

const RAW = JSON.parse(readFileSync(path.join(__dirname, "..", "public", "showcase", "ed90", "showcase.json"), "utf8")) as Record<string, unknown>;

/** The summary block the engine's export now writes (lull.showcase.run_summary). */
const SUMMARY = {
  counts: { verified: 7, edited: 0, approved: 1, held: 0, extended: 2 },
  coverage: { importantEvents: 9, described: 8, ratio: 0.889, describedWithExtended: 9, ratioWithExtended: 1, undescribed: ["e12"] },
  repetitive: [{ id: "e09", text: "Wire figures sway." }],
  f113: [{ gapId: "g009", start: 64.3, end: 66.5, eventIds: ["e12"] }],
  overlapSeconds: 0,
  wcag: [
    { criterion: "1.2.5", level: "AA", name: "Audio Description (Prerecorded)", status: "needs review", blockers: ["1 pause of 2 s or more left unused while important visual content goes undescribed (F113)"] },
  ],
  models: ["nvidia/Nemotron-3-Ultra-550b-a55b"],
  spend: { modelUsdThisRun: 0.0364, webSearchesThisRun: 2, webSearchListUsdThisRun: 0.016, modelUsdAllRunsOfRunId: 0.0611 },
};

describe("a recorded run's report", () => {
  it("without a summary in its export: says so, and counts only its published lines", () => {
    const s = parseShowcase(RAW, "ed90");
    const r = showcaseReport(s, fromShowcase(s));
    expect(r.noSummary).toBe(true);
    expect(r.wcag).toBeNull();
    expect(r.coverage).toBeNull();
    expect(r.overlapSeconds).toBeNull();
    expect(r.reviewed).toBeNull();
    expect(r.countsFrom).toBe("lines");
    expect(r.counts).toEqual({ verified: 8, extended: 2 });
    expect(r.spend).toEqual([{ name: "Model calls", value: "$0.0364", note: "As the export gives it." }]);
  });

  it("with the export's summary: every row from it, spend labelled as the engine labels it", () => {
    const s = parseShowcase({ ...RAW, summary: SUMMARY }, "ed90");
    const r = showcaseReport(s, fromShowcase(s));
    expect(r.noSummary).toBe(false);
    expect(r.countsFrom).toBe("summary");
    expect(r.counts.approved).toBe(1);
    expect(r.coverage).toEqual({ important: 9, described: 8, ratio: 0.889, describedWithExtended: 9, ratioWithExtended: 1, undescribed: ["e12"] });
    expect(r.wcag?.[0]).toMatchObject({ criterion: "1.2.5", status: "needs review", blockers: [expect.stringContaining("F113")] });
    expect(r.f113).toEqual([{ gapId: "g009", start: 64.3, end: 66.5, events: ["e12"] }]);
    expect(r.spend?.map((x) => x.name)).toEqual(["Model calls, this run", "Model calls, every run under this id", "Web searches, this run"]);
    expect(r.spend?.[2]?.note).toBe("$0.0160 at list price, not charged.");
  });

  it("a summary from before these were recorded leaves its rows out", () => {
    const empty = { counts: {}, coverage: {}, repetitive: [], f113: [], overlapSeconds: null, wcag: [], models: [], spend: {} };
    const s = parseShowcase({ ...RAW, summary: empty }, "ed90");
    const r = showcaseReport(s, fromShowcase(s));
    expect(r.wcag).toBeNull();
    expect(r.coverage).toBeNull();
    expect(r.models).toBeNull();
    expect(r.countsFrom).toBe("lines");
  });

  it("refuses a malformed summary, naming the field", () => {
    expect(() => parseShowcase({ ...RAW, summary: { wcag: [{ level: "AA" }] } }, "ed90")).toThrow(/summary\.wcag\[0\]/);
    expect(() => parseShowcase({ ...RAW, summary: [] }, "ed90")).toThrow(/summary/);
  });
});

describe("a live run's report", () => {
  it("reads the manifest's summary: WCAG with its blockers, held lines, review, models and spend", () => {
    const detail = readDetail({
      manifest: {
        run_id: "r1",
        video: { name: "clip.mp4", duration: 30 },
        outputs: { "report.html": { bytes: 100 } },
        summary: {
          cues: { verified: 3, held: 1 },
          coverage: { important_events: 6, described: 3, ratio: 0.5, undescribed: [] },
          held: [{ id: "c003", reason: "no door" }],
          overlap_seconds: 0,
          wcag: [{ criterion: "1.2.7", level: "AAA", name: "Extended", status: "not claimed", via: "", reasons: ["extended version not produced"] }],
          reviewed: false,
          cost_usd: 0.0213,
          cost_per_min: 0.0142,
          spend: { models_usd: 0.0213, tavily_credits: 1 },
          tts_disclosure: "Descriptions are voiced with synthetic speech (NVIDIA Magpie TTS); lines are awaiting review.",
        },
        versions: { models: ["nvidia/Nemotron-3.5-Super-VL"] },
      },
      cues: { cues: [], extended: [] },
      dirty: false,
    })!;
    const r = apiReport("r1", detail, fromApi("r1", detail));
    expect(r.kind).toBe("live");
    expect(r.held).toEqual([{ id: "c003", reason: "no door" }]);
    expect(r.reviewed).toBe(false);
    expect(r.wcag?.[0]).toMatchObject({ status: "not claimed", blockers: ["extended version not produced"], via: null });
    expect(r.spend).toEqual([
      { name: "Model calls", value: "$0.0213", note: "$0.0142 per minute of video." },
      { name: "Web search credits", value: "1", note: "Counted apart from the model spend." },
    ]);
    expect(r.engineReport).toMatch(/\/v1\/runs\/r1\/files\/report\.html$/);
    expect(r.f113).toBeNull();
  });
});
