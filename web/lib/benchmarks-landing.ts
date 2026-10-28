import type { Benchmarks, Ratio } from "./benchmarks-schema";
import { ablationSentence } from "./benchmarks-text";

export const BENCHMARK_KEYS = ["overlap", "hallucination", "recall", "costPerMinute"] as const;
export type BenchmarkKey = (typeof BENCHMARK_KEYS)[number];
export type LandingCell = { value: string; detail?: string };

const pct = (x: number) => `${(x * 100).toFixed(1)} %`;
const interval = (r: Ratio) => (r.ci95 ? `; 95 % interval ${(r.ci95[0] * 100).toFixed(1)}–${(r.ci95[1] * 100).toFixed(1)} %` : "");

const span = (xs: number[]) => {
  const lo = Math.min(...xs) * 100;
  const hi = Math.max(...xs) * 100;
  return lo.toFixed(1) === hi.toFixed(1) ? `${lo.toFixed(1)} %` : `${lo.toFixed(1)}–${hi.toFixed(1)} %`;
};

/**
 * What the rest of the evaluation found, in plain sentences read from the results (complete,
 * published runs only): how much of what human describers said Lull conveyed, against the
 * human-to-human ceiling; and what the frame check does and doesn't do.
 */
export function landingFindings(b: Benchmarks | null): { human: string | null; check: string | null } {
  if (!b || b.fixture) return { human: null, check: null };
  let human: string | null = null;
  const sb = b.suiteB;
  if (sb && sb.complete && !sb.smoke) {
    const lull = sb.references.map((r) => r.recall.bothWindow.value).filter((v): v is number => v !== null);
    const people = sb.baseline.map((x) => x.recall.standardWindow.value).filter((v): v is number => v !== null);
    if (lull.length && people.length)
      human = `Against human audio description of the same videos, Lull conveyed ${span(lull)} of the facts the describers chose to say; two people describing the same film shared ${span(people)}. A person can add the rest in the studio.`;
  }
  const parts: string[] = [];
  const neg = b.negative;
  if (neg && neg.complete && neg.headline.falseAccept.value !== null)
    parts.push(
      `Checked against the wrong footage, the frame check still passed ${pct(neg.headline.falseAccept.value)} of lines (${neg.headline.falseAccept.num} of ${neg.headline.falseAccept.den}, an upper bound).`,
    );
  if (b.ablation && b.ablation.complete) {
    const s = ablationSentence(b.ablation);
    if (s) parts.push(s);
  }
  return { human, check: parts.length ? parts.join(" ") : null };
}

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
