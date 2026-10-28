/**
 * A run's conformance report, from whichever source has it: a recorded run's export (its
 * `summary`, engine lull.showcase.run_summary) or a live run's manifest (GET /v1/runs/{id}).
 * Every row comes from the data; a field the source doesn't carry is null and the page leaves
 * its row out. Counts by status fall back to the published lines when no summary says them,
 * and are marked so.
 */
import type { CueStatus } from "./api";
import { reportUrl } from "./api";
import type { DoneDetail } from "./api-runs";
import type { RunData } from "./run-data";
import type { Showcase } from "./showcase-schema.mjs";

export type ReportWcag = { criterion: string; level: string; name: string; status: string; blockers: string[]; via: string | null };

export type SpendRow = { name: string; value: string; note?: string };

export type ReportData = {
  id: string;
  kind: "recorded" | "live";
  title: string;
  credit: string | null;
  duration: number;
  /** The summary was missing from the source: only what the published lines show is reported. */
  noSummary: boolean;
  counts: Partial<Record<CueStatus, number>>;
  countsFrom: "summary" | "lines";
  coverage: {
    important: number;
    described: number | null;
    ratio: number | null;
    describedWithExtended: number | null;
    ratioWithExtended: number | null;
    undescribed: string[];
  } | null;
  repetitive: { id: string; text: string | null }[] | null;
  f113: { gapId: string; start: number; end: number; events: string[] }[] | null;
  overlapSeconds: number | null;
  held: { id: string; reason: string | null }[] | null;
  wcag: ReportWcag[] | null;
  /** Every published line was approved or edited by a person (the data's own word). */
  reviewed: boolean | null;
  ttsDisclosure: string | null;
  models: string[] | null;
  spend: SpendRow[] | null;
  /** The engine's own report.html, when the run is served by the API. */
  engineReport: string | null;
};

const usd = (x: number, digits = 4) => `$${x.toFixed(digits)}`;

function countLines(run: RunData): Partial<Record<CueStatus, number>> {
  const out: Partial<Record<CueStatus, number>> = {};
  for (const c of run.cues) out[c.status] = (out[c.status] ?? 0) + 1;
  return out;
}

/** A recorded run: its export's summary when it has one. */
export function showcaseReport(s: Showcase, run: RunData): ReportData {
  const sum = s.summary;
  const counts = sum && Object.keys(sum.counts).length ? sum.counts : null;
  const cov = sum?.coverage;
  const spend: SpendRow[] = [];
  if (sum?.spend.modelUsdThisRun != null)
    spend.push({ name: "Model calls, this run", value: usd(sum.spend.modelUsdThisRun), note: "The attempt that published it." });
  if (sum?.spend.modelUsdAllRunsOfRunId != null)
    spend.push({ name: "Model calls, every run under this id", value: usd(sum.spend.modelUsdAllRunsOfRunId), note: "Earlier attempts included." });
  // searches are counted, never priced
  if (sum?.spend.webSearchesThisRun != null) spend.push({ name: "Web searches, this run", value: String(sum.spend.webSearchesThisRun) });
  if (!spend.length && s.costUsd !== undefined) spend.push({ name: "Model calls", value: usd(s.costUsd), note: "As the export gives it." });
  return {
    id: s.id,
    kind: "recorded",
    title: s.title,
    credit: s.credit,
    duration: s.duration,
    noSummary: !sum,
    counts: counts ?? countLines(run),
    countsFrom: counts ? "summary" : "lines",
    coverage: cov && cov.importantEvents !== null ? { important: cov.importantEvents, described: cov.described, ratio: cov.ratio, describedWithExtended: cov.describedWithExtended, ratioWithExtended: cov.ratioWithExtended, undescribed: cov.undescribed } : null,
    repetitive: sum ? sum.repetitive : null,
    f113: sum ? sum.f113.map((f) => ({ gapId: f.gapId, start: f.start, end: f.end, events: f.eventIds })) : null,
    overlapSeconds: sum?.overlapSeconds ?? null,
    held: null,
    wcag: sum && sum.wcag.length ? sum.wcag.map((w) => ({ ...w, via: null })) : null,
    reviewed: null,
    ttsDisclosure: null,
    models: sum && sum.models.length ? sum.models : null,
    spend: spend.length ? spend : null,
    engineReport: null,
  };
}

/** A run the API serves: its manifest's summary. */
export function apiReport(id: string, detail: DoneDetail, run: RunData): ReportData {
  const s = detail.manifest.summary;
  const counts = Object.keys(s.cues).length ? s.cues : null;
  const spend: SpendRow[] = [];
  const model = s.spend?.modelUsd ?? s.costUsd;
  if (model !== null)
    spend.push({ name: "Model calls, this run", value: usd(model), note: s.costPerMin !== null ? `${usd(s.costPerMin)} per minute of video.` : undefined });
  const all = s.spend?.modelUsdAllRuns ?? null;
  if (all !== null && model !== null && all > model) spend.push({ name: "Model calls, every run under this id", value: usd(all), note: "Earlier attempts included." });
  // searches are counted, never priced
  if (s.spend?.searches != null) spend.push({ name: "Web searches, this run", value: String(s.spend.searches) });
  return {
    id,
    kind: "live",
    title: run.title,
    credit: run.credit,
    duration: run.duration,
    noSummary: false,
    counts: counts ?? countLines(run),
    countsFrom: counts ? "summary" : "lines",
    coverage:
      s.coverage && s.coverage.important !== null
        ? {
            important: s.coverage.important,
            described: s.coverage.described,
            ratio: s.coverage.ratio,
            describedWithExtended: s.coverage.describedWithExtended,
            ratioWithExtended: s.coverage.ratioWithExtended,
            undescribed: s.coverage.undescribed,
          }
        : null,
    repetitive: s.repetitive,
    f113: s.f113 ? s.f113.map((f) => ({ gapId: f.gapId, start: f.start, end: f.end, events: [] })) : null,
    overlapSeconds: s.overlapSeconds,
    held: s.held,
    wcag: s.wcag && s.wcag.length ? s.wcag.map((w) => ({ criterion: w.criterion, level: w.level, name: w.name, status: w.status, blockers: w.reasons, via: w.via || null })) : null,
    reviewed: s.reviewed,
    ttsDisclosure: s.ttsDisclosure,
    models: detail.manifest.versions.models.length ? detail.manifest.versions.models : null,
    spend: spend.length ? spend : null,
    engineReport: "report.html" in detail.manifest.outputs ? reportUrl(id) : null,
  };
}
