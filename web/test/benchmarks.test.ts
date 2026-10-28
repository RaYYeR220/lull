import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { landingMetrics } from "../lib/benchmarks-landing";
import { BenchmarksError, costPerMinute, parseBenchmarks, preregCalls } from "../lib/benchmarks-schema";
import { benchmarksFile } from "../lib/benchmarks-file";

const FIXTURE = JSON.parse(readFileSync(path.join(__dirname, "..", "e2e", "fixtures", "benchmarks.sample.json"), "utf8")) as Record<string, unknown>;
const clone = () => structuredClone(FIXTURE) as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

describe("benchmarks.json", () => {
  it("reads the scorer's own suite_a.json, as the fixture carries it", () => {
    const b = parseBenchmarks(FIXTURE);
    expect(b.fixture).toBe(true);
    expect(b.knownFailures).toHaveLength(2);
    const a = b.suiteA!;
    expect(a.complete).toBe(false);
    expect(a.note).toMatch(/partial run/);
    expect(a.preregistration).toEqual({ tag: "suite-a-prereg", commit: "194d4d0803aa4a9195abbb450b35d7d132631ae1", committed: "2026-10-28T02:12:13+01:00" });
    expect(a.keySha256).toBe("354967ad20310ab6013c5fd884520f57120dbaa56d81b927f2d0c3ec35b7ce96");
    expect(a.aggregate.recallStandard).toMatchObject({ num: 3, den: 41 });
    expect(a.aggregate.recallStandard.ci95?.[0]).toBeCloseTo(0.0588, 3);
    expect(a.aggregate.recallWithExtended).toMatchObject({ num: 9, den: 41 });
    expect(a.aggregate.decoysStated).toBe(0);
    expect(a.aggregate.decoysTotal).toBe(8);
    expect(a.aggregate.cost.modelUsdPerMin).toBeCloseTo(0.023789, 6);
    expect(a.aggregate.processing.sPerMin).toBe(87.7);
    expect(a.secondJudge?.agreement.facts).toMatchObject({ n: 41, agree: 38 });
    expect(a.secondJudge?.agreement.claims?.kappa).toBeNull();
    expect(a.hardware?.cpu).toMatch(/Ryzen/);
    expect(a.videos.map((v) => v.id)).toEqual(["a01", "a02"]);
    expect(a.videos[0]).toMatchObject({ subject: "biology", silence: { pauses: 9, pausesGe2s: 7 }, recallStandard: [2, 24], decoys: { stated: 0, of: 4 } });
    expect(a.probe?.facts).toEqual([24, 24]);
  });

  it("ignores fields it doesn't know, and notes other suites without drawing them", () => {
    const raw = clone();
    raw.extra = { anything: true };
    raw.suites.suite_a.aggregate.something_new = 1;
    raw.suites.suite_c = { videos: [] };
    const b = parseBenchmarks(raw);
    expect(b.otherSuites).toEqual(["suite_c"]);
    expect(b.suiteA?.aggregate.recallStandard.num).toBe(3);
  });

  it("an evaluation with no suite yet is still a file", () => {
    const b = parseBenchmarks({ generated_at: "2026-10-30T00:00:00Z", git_commit: "abc", suites: {}, known_failures: [] });
    expect(b.suiteA).toBeNull();
    expect(b.fixture).toBe(false);
  });

  it("names the first field that doesn't hold, so a broken file never shows half a number", () => {
    const noTag = clone();
    delete noTag.suites.suite_a.preregistration.tag;
    expect(() => parseBenchmarks(noTag)).toThrow(BenchmarksError);
    expect(() => parseBenchmarks(noTag)).toThrow(/suites\.suite_a\.preregistration\.tag must be a non-empty string/);
    const badRatio = clone();
    badRatio.suites.suite_a.aggregate.recall_standard = { value: 0.1 };
    expect(() => parseBenchmarks(badRatio)).toThrow(/aggregate\.recall_standard\.num must be a number/);
    const noFailures = clone();
    delete noFailures.known_failures;
    expect(() => parseBenchmarks(noFailures)).toThrow(/known_failures must be a list/);
    expect(() => parseBenchmarks([])).toThrow(/must be a JSON object/);
  });

  it("the landing's four cells come from the published suite, never from test data", () => {
    const real = clone();
    delete real.fixture;
    // a partial run (a smoke) isn't results either
    expect(landingMetrics(parseBenchmarks(real))).toEqual({});
    real.suites.suite_a.complete = true;
    const cells = landingMetrics(parseBenchmarks(real));
    expect(cells.overlap?.value).toBe("0.000 s");
    expect(cells.recall?.value).toBe("7.3 %");
    expect(cells.recall?.detail).toMatch(/3 of 41 visual facts, standard track; 95 % interval 5.9–8.3 %/);
    expect(cells.hallucination?.value).toBe("0.0 %");
    expect(cells.costPerMinute?.value).toBe("$0.024");
    expect(landingMetrics(parseBenchmarks(FIXTURE))).toEqual({});
  });
});

describe("what the page says from the results", () => {
  it("the cost per minute pairs each value with its own interval: model and web searches together, model alone without one", () => {
    const g = parseBenchmarks(FIXTURE).suiteA!.aggregate;
    const c = costPerMinute(g);
    expect(c.combined).toBeCloseTo(0.023789 + 0, 6);
    expect(c.combinedCi?.[0]).toBeCloseTo(0.022768, 5);
    expect(c.modelOnly).toBeCloseTo(0.023789, 6);
    expect(c.modelOnlyCi).toBeNull();
    // a scorer that also writes the model-only interval gets it shown
    const raw = clone();
    raw.suites.suite_a.aggregate.cost.model_per_min_ci95 = [0.02, 0.03];
    raw.suites.suite_a.aggregate.cost.tavily_usd_per_min = 0.004;
    const c2 = costPerMinute(parseBenchmarks(raw).suiteA!.aggregate);
    expect(c2.modelOnlyCi).toEqual([0.02, 0.03]);
    expect(c2.combined).toBeCloseTo(0.027789, 6);
    expect(c2.webSearches).toBeCloseTo(0.004, 6);
  });

  it("tells calls that came before the key apart from calls not found at all", () => {
    const a = parseBenchmarks(FIXTURE).suiteA!;
    expect(preregCalls({ ...a, callsAfterPreregistration: true })).toBe("Every model call the results use came after the pre-registration commit.");
    expect(preregCalls({ ...a, callsAfterPreregistration: false, spendUsd: 0.2 })).toBe("Some model calls the results use came before the pre-registration commit.");
    expect(preregCalls({ ...a, callsAfterPreregistration: false, spendUsd: 0 })).toBe("No model spend for these results was found in the ledger, so when their calls were made can’t be checked.");
    expect(preregCalls({ ...a, callsAfterPreregistration: false, spendUsd: null })).toMatch(/^No model spend for these results was found/);
    expect(preregCalls({ ...a, callsAfterPreregistration: null })).toBeNull();
  });
});

describe("which file the page is built from", () => {
  it("public/benchmarks.json, unless the build names another (the e2e build names the fixture)", () => {
    expect(benchmarksFile({}, "/site")).toBe(path.resolve("/site", "public", "benchmarks.json"));
    expect(benchmarksFile({ LULL_BENCHMARKS_FILE: "e2e/fixtures/benchmarks.sample.json" }, "/site")).toBe(path.resolve("/site", "e2e", "fixtures", "benchmarks.sample.json"));
  });
});
