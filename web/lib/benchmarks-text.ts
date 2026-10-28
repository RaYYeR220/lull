import type { Ablation, AblationRow, SuiteB, SuiteBRecall } from "./benchmarks-schema";

/** Who described the reference, as the page names them. */
export function referenceName(id: string): string {
  return (
    {
      pfeiffer: "Elephants Dream, described by Silvia Pfeiffer",
      hunt: "Elephants Dream, described by JJ Hunt",
      usgs_ad: "USGS videos, the agency’s own audio description",
    }[id] ?? id.replace(/_/g, " ")
  );
}

/** A track or side in Suite B's overlap and density tables. */
export function sideName(side: string): string {
  return (
    {
      lull_standard: "Lull, standard track",
      lull_with_extended: "Lull, with extended",
      pfeiffer_vtt: "Silvia Pfeiffer’s captions file",
      pfeiffer: "Silvia Pfeiffer",
      hunt_vtt: "JJ Hunt’s captions file",
      hunt_wav: "JJ Hunt’s voiced track",
      hunt: "JJ Hunt",
      usgs_ad: "USGS AD, per minute of the original",
      usgs_ad_per_ad_version_min: "USGS AD, per minute of the AD version",
    }[side] ?? side.replace(/_/g, " ")
  );
}

/**
 * The human ceiling for a reference: the other describer's lines judged against this
 * describer's facts (baseline "<other>_lines_vs_<ref>_facts"); null where one person described the video.
 */
export function humanCeiling(b: Pick<SuiteB, "baseline">, ref: string): SuiteBRecall | null {
  return b.baseline.find((x) => x.id.endsWith(`_vs_${ref}_facts`))?.recall ?? null;
}

/** The ablation's measures, named as on the rest of the page. */
export const ABLATION_NAMES: Record<string, string> = {
  hallucination_claims: "Unsupported claims",
  hallucination_cues: "Lines with an unsupported claim",
  hallucination_voiced_text: "Unsupported statements in the voiced text",
  voiced_cues_with_unsupported: "Voiced lines with an unsupported statement",
  recall_standard: "Visual facts described, standard track",
  recall_with_extended: "Visual facts described, with the extended track",
  decoys_stated: "Decoys stated",
};

const HALLUCINATION = ["hallucination_claims", "hallucination_cues", "hallucination_voiced_text", "voiced_cues_with_unsupported"];
const spans0 = (r: AblationRow) => r.ci95 !== null && r.ci95[0] <= 0 && r.ci95[1] >= 0;
const points = (x: number) => `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(1)}`;

/**
 * The ablation in one plain sentence, read from its numbers: whether verification measurably
 * reduced the unsupported-statement measures (every interval of without-minus-with spanning 0
 * means it did not), and what it did to recall with the extended track.
 */
export function ablationSentence(a: Pick<Ablation, "comparison">): string | null {
  const hall = a.comparison.filter((r) => HALLUCINATION.includes(r.metric));
  const recall = a.comparison.find((r) => r.metric === "recall_with_extended");
  if (!hall.length || hall.some((r) => r.ci95 === null)) return null;
  const parts: string[] = [];
  if (hall.every(spans0)) parts.push("On these synthetic lectures, checking each line against its frames did not measurably reduce unsupported statements");
  else parts.push("On these synthetic lectures, checking each line against its frames changed some unsupported-statement measures beyond chance; see the table");
  if (recall && recall.ci95 && recall.ci95[0] > 0)
    parts.push(
      `and it cost recall: with the extended track, ${(recall.with * 100).toFixed(1)} % of visual facts were described with the check and ${(recall.without * 100).toFixed(1)} % without it (${points(recall.difference)} points, 95 % interval ${points(recall.ci95[0])} to ${points(recall.ci95[1])})`,
    );
  return `${parts.join(", ")}.`;
}
