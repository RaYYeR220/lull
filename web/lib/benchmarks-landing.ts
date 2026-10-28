import type { Benchmarks, Ratio } from "./benchmarks-schema";

export const BENCHMARK_KEYS = ["overlap", "hallucination", "recall", "costPerMinute"] as const;
export type BenchmarkKey = (typeof BENCHMARK_KEYS)[number];
export type LandingCell = { value: string; detail?: string };

const pct = (x: number) => `${(x * 100).toFixed(1)} %`;
const interval = (r: Ratio) => (r.ci95 ? `; 95 % interval ${(r.ci95[0] * 100).toFixed(1)}–${(r.ci95[1] * 100).toFixed(1)} %` : "");

/**
 * The landing's four cells, from the published Suite A results: only a complete scored run,
 * never a partial one and never test data. A cell the results can't fill stays empty.
 */
export function landingMetrics(b: Benchmarks | null): Partial<Record<BenchmarkKey, LandingCell>> {
  const a = b?.suiteA;
  if (!b || b.fixture || !a || !a.complete) return {};
  const g = a.aggregate;
  const out: Partial<Record<BenchmarkKey, LandingCell>> = {
    overlap: { value: `${g.overlapSTotal.toFixed(3)} s`, detail: `Seconds of description over narration, across ${g.videos} videos.` },
  };
  if (g.hallucinationClaims.value !== null)
    out.hallucination = {
      value: pct(g.hallucinationClaims.value),
      detail: `${g.hallucinationClaims.num} of ${g.hallucinationClaims.den} claims unsupported${interval(g.hallucinationClaims)}.`,
    };
  if (g.recallStandard.value !== null)
    out.recall = {
      value: pct(g.recallStandard.value),
      detail: `${g.recallStandard.num} of ${g.recallStandard.den} visual facts, standard track${interval(g.recallStandard)}.`,
    };
  if (g.cost.modelUsdPerMin !== null)
    out.costPerMinute = { value: `$${g.cost.modelUsdPerMin.toFixed(3)}`, detail: "Model spend per minute of video; web searches are counted apart." };
  return out;
}
