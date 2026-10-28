import { readFileSync } from "node:fs";
import path from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ControlsView } from "../components/benchmarks/ControlsView";
import { SuiteBView } from "../components/benchmarks/SuiteBView";
import { landingFindings, landingMetrics } from "../lib/benchmarks-landing";
import { parseBenchmarks } from "../lib/benchmarks-schema";
import { ablationSentence, humanCeiling } from "../lib/benchmarks-text";

/** The published evaluation, as the site ships it. */
const FILE = path.join(__dirname, "..", "public", "benchmarks.json");
const TEXT = readFileSync(FILE, "utf8");
const b = parseBenchmarks(JSON.parse(TEXT));
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

describe("the published benchmarks file", () => {
  it("is the evaluation, not test data, and names no local path", () => {
    expect(b.fixture).toBe(false);
    for (const local of ["C:\\\\", "C:/", "/Users/", "/home/"]) expect(TEXT.includes(local), local).toBe(false);
  });

  it("carries all four suites, complete, and the known failures the eval lists", () => {
    expect(b.suiteA?.complete).toBe(true);
    expect(b.suiteB?.complete).toBe(true);
    expect(b.negative?.complete).toBe(true);
    expect(b.ablation?.complete).toBe(true);
    expect(b.otherSuites).toEqual([]);
    const titles = b.knownFailures.map((f) => f.title);
    for (const t of ["Extended freezes fall inside continuous narration", "Suite B woodshole: the standard track voiced nothing", "The two Suite B judges disagree"])
      expect(titles).toContain(t);
  });
});

describe("Suite B on the page", () => {
  const sb = b.suiteB!;
  const html = renderToStaticMarkup(<SuiteBView b={sb} />);

  it("puts the human ceiling next to Lull’s number, with every interval and count", () => {
    expect(humanCeiling(sb, "pfeiffer")?.standardWindow).toMatchObject({ num: 59, den: 152 });
    expect(humanCeiling(sb, "hunt")?.standardWindow).toMatchObject({ num: 53, den: 124 });
    expect(humanCeiling(sb, "usgs_ad")).toBeNull();
    const pfeiffer = html.slice(html.indexOf('data-reference="pfeiffer"'));
    const row = text(pfeiffer.slice(pfeiffer.indexOf('data-row="window"'), pfeiffer.indexOf('data-row="anywhere"')));
    // Lull standard, Lull with extended, then the other describer, in one row
    expect(row).toContain("1.3 % [0.0 %, 3.4 %] (2/152)");
    expect(row).toContain("38.8 % [27.6 %, 50.6 %] (59/152)");
  });

  it("USGS has no human ceiling, weighs window and anywhere equally, and its all-facts recall is a lower bound", () => {
    const usgs = html.slice(html.indexOf('data-reference="usgs_ad"'));
    expect(text(usgs.slice(0, usgs.indexOf("</table>")))).toContain("None: one describer for this video");
    expect(text(usgs.slice(0, usgs.indexOf("</table>"))).match(/pre-registered, equal weight/g)).toHaveLength(2);
    const lower = html.slice(html.indexOf('data-lower-bound="usgs_ad"'));
    expect(text(lower.slice(0, lower.indexOf("</p>")))).toContain("4.2 % [1.1 %, 8.2 %] (8/189)");
    expect(text(lower.slice(0, lower.indexOf("</p>")))).toContain("lower bound");
  });

  it("labels Lull’s own-span overlap as 0 by construction, and the judges’ agreement with kappa", () => {
    expect(text(html)).toContain("0 by construction against its own speech detection");
    expect(text(html)).toContain("display time, not voiced");
    expect(text(html)).toContain("94.4 % agree, kappa 0.21 (n = 124)");
    expect(text(html)).toContain("Supplementary: reported beside the primary judge");
  });
});

describe("the controls on the page", () => {
  const html = text(renderToStaticMarkup(<ControlsView negative={b.negative} ablation={b.ablation} />));

  it("negative control: an upper-bound headline, the discriminative 0/11, per-rule rows", () => {
    expect(html).toContain("False accept, an upper bound");
    expect(html).toContain("13.2 % [2.6 %, 23.7 %] (5/38)");
    expect(html).toContain("0.0 % [0.0 %, 0.0 %] (0/11)");
    expect(html).toContain("True accept, optimistic");
    expect(html).toContain("Other events in the same video, at least 30 s away");
    expect(html).toContain("18.5 % [3.7 %, 33.3 %] (5/27)");
  });

  it("ablation: both arms, the paired difference with its interval, and the plain sentence", () => {
    expect(html).toContain("14.0 %");
    expect(html).toContain("28.9 %");
    expect(html).toContain("+14.9 % [+6.2 %, +23.1 %]");
    const s = ablationSentence(b.ablation!);
    expect(s).toBe(
      "On these synthetic lectures, checking each line against its frames did not measurably reduce unsupported statements, and it cost recall: with the extended track, 14.0 % of visual facts were described with the check and 28.9 % without it (+14.9 points, 95 % interval +6.2 to +23.1).",
    );
    expect(html).toContain(s!);
  });

  it("says something else when an unsupported-statement interval clears zero", () => {
    const moved = { comparison: b.ablation!.comparison.map((r) => (r.metric === "hallucination_claims" ? { ...r, ci95: [0.01, 0.2] as [number, number] } : r)) };
    expect(ablationSentence(moved)).not.toMatch(/did not measurably reduce/);
  });
});

describe("the landing reads only these results", () => {
  it("the four cells, unrounded beyond one decimal", () => {
    const cells = landingMetrics(b);
    expect(cells.overlap?.value).toBe("0.000 s");
    expect(cells.hallucination?.value).toBe("18.1 %");
    expect(cells.hallucination?.detail).toBe("19 of 105 claims unsupported; 95 % interval 4.0–37.2 %.");
    expect(cells.recall?.value).toBe(`${(b.suiteA!.aggregate.recallStandard.value! * 100).toFixed(1)} %`);
  });

  it("what the rest found, in plain sentences from the file", () => {
    const f = landingFindings(b);
    expect(f.human).toBe(
      "Against human audio description of the same videos, Lull conveyed 0.8–4.5 % of the facts the describers chose to say; two people describing the same film shared 38.8–42.7 %. A person can add the rest in the studio.",
    );
    expect(f.check).toMatch(/^Checked against the wrong footage, the frame check still passed 13\.2 % of lines \(5 of 38, an upper bound\)\. On these synthetic lectures/);
    // test data never reaches the landing
    expect(landingFindings({ ...b, fixture: true })).toEqual({ human: null, check: null });
  });
});
