/**
 * public/benchmarks.json: what the evaluation published, as the page reads it.
 *
 *   { generated_at, git_commit, fixture?, suites: { suite_a?, suite_b?, controls? },
 *     known_failures: [{ title, detail }] }
 *
 * suite_a is the Suite A scorer's own results file, unchanged (eval/suite_a/score.py writes
 * results/suite_a.json). The validator reads the fields the page shows and ignores the rest;
 * a field the page needs that is missing or of the wrong kind is an error naming it, so a
 * broken file never shows a half-made number. `fixture: true` marks test data: the page says
 * so on every view, and a production build refuses it.
 */

export class BenchmarksError extends Error {
  constructor(where: string, what: string) {
    super(`benchmarks.json: ${where} ${what}`);
    this.name = "BenchmarksError";
  }
}

/** A pooled ratio with its 95 % bootstrap interval (score.py bootstrap_ratio). */
export type Ratio = { value: number | null; num: number; den: number; ci95: [number, number] | null };
/** [count, of] as the scorer keeps per video. */
export type Count = [number, number];
export type Agreement = { n: number; agree: number; agreement: number | null; kappa: number | null };

export type SuiteAVideo = {
  id: string;
  subject: string;
  durationS: number;
  failed: string | null;
  silence: { seconds: number; pauses: number; pausesGe2s: number } | null;
  published: { standard: number; extended: number } | null;
  overlapS: number | null;
  recallStandard: Count | null;
  recallWithExtended: Count | null;
  hallucinationClaims: Count | null;
  decoys: { stated: number; of: number } | null;
  costModelUsd: number | null;
  costTavilyUsd: number | null;
  processingS: number | null;
};

export type SuiteA = {
  complete: boolean;
  note: string | null;
  date: string;
  gitCommit: string;
  gitDirty: boolean | null;
  preregistration: { tag: string; commit: string; committed: string };
  keySha256: string;
  callsAfterPreregistration: boolean | null;
  judgeModel: string | null;
  judgeNote: string | null;
  secondJudge: {
    model: string;
    recall: Ratio | null;
    hallucination: Ratio | null;
    hallucinationVoicedText: Ratio | null;
    agreement: { facts: Agreement | null; claims: Agreement | null; voicedCues: Agreement | null };
  } | null;
  engineModels: string[];
  runOptions: { mode: string | null; profile: string | null; voice: string | null; research: boolean | null } | null;
  hardware: { os: string | null; cpu: string | null; gpus: string[]; speechServer: string | null; models: string | null } | null;
  spendUsd: number | null;
  aggregate: {
    videos: number;
    failedRuns: string[];
    minutes: number;
    silenceS: number | null;
    pauses: number | null;
    pausesGe2s: number | null;
    overlapSTotal: number;
    extendedFreezeInSpeech: number | null;
    capacityViolations: number | null;
    recallStandard: Ratio;
    recallWithExtended: Ratio;
    timelyRecallWithExtended: Ratio | null;
    hallucinationClaims: Ratio;
    hallucinationCues: Ratio | null;
    redundancyCues: Ratio;
    spokenFactsRestated: Ratio | null;
    decoysStated: number;
    decoysTotal: number;
    decoyNumberHits: number | null;
    // supplementary: added after the pre-registration
    recallTieAwareStandard: Ratio | null;
    recallTieAwareWithExtended: Ratio | null;
    hallucinationVoicedText: Ratio | null;
    voicedUnverifiable: Ratio | null;
    voicedCuesWithUnsupported: Ratio | null;
    claimsNotInCueText: Ratio | null;
    hallucinationClaimsInCueText: Ratio | null;
    decoysAbsentPart: number | null;
    cost: { modelUsd: number; tavilyUsd: number; modelUsdPerMin: number | null; tavilyUsdPerMin: number | null; perMinCi95: [number, number] | null };
    processing: { seconds: number; sPerMin: number | null; sPerMinCi95: [number, number] | null };
    cueCounts: Record<string, number>;
  };
  probe: {
    facts: Count | null;
    decoys: Count | null;
    negativesNotCredited: Count | null;
    voicedSensitivity: Count | null;
    voicedSpecificity: Count | null;
  } | null;
  videos: SuiteAVideo[];
};

export type Benchmarks = {
  generatedAt: string;
  gitCommit: string;
  /** Test data, never the published evaluation. */
  fixture: boolean;
  suiteA: SuiteA | null;
  /** Other suites present in the file (not drawn on this page yet). */
  otherSuites: string[];
  knownFailures: { title: string; detail: string }[];
};

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** Readers that name the field when it doesn't hold. */
function at(o: Obj, key: string, where: string) {
  const v = o[key];
  const here = `${where}.${key}`;
  return {
    str: (): string => {
      if (typeof v !== "string" || !v) throw new BenchmarksError(here, "must be a non-empty string");
      return v;
    },
    num: (): number => {
      if (!isNum(v)) throw new BenchmarksError(here, "must be a number");
      return v;
    },
    obj: (): Obj => {
      if (!isObj(v)) throw new BenchmarksError(here, "must be an object");
      return v;
    },
    optStr: (): string | null => (typeof v === "string" && v ? v : null),
    optNum: (): number | null => (isNum(v) ? v : null),
    optBool: (): boolean | null => (typeof v === "boolean" ? v : null),
    optObj: (): Obj | null => (isObj(v) ? v : null),
    strings: (): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []),
  };
}

function pair(v: unknown): [number, number] | null {
  return Array.isArray(v) && v.length === 2 && isNum(v[0]) && isNum(v[1]) ? [v[0], v[1]] : null;
}

function ratio(v: unknown, where: string): Ratio {
  if (!isObj(v)) throw new BenchmarksError(where, "must be a ratio {value, num, den, ci95}");
  const r = at(v, "num", where).num();
  const d = at(v, "den", where).num();
  const value = v.value === null ? null : at(v, "value", where).num();
  return { value, num: r, den: d, ci95: pair(v.ci95) };
}

const optRatio = (v: unknown, where: string): Ratio | null => (v === undefined || v === null ? null : isObj(v) ? ratio(v, where) : null);

function agreementOf(v: unknown): Agreement | null {
  if (!isObj(v) || !isNum(v.n) || !isNum(v.agree)) return null;
  return { n: v.n, agree: v.agree, agreement: isNum(v.agreement) ? v.agreement : null, kappa: isNum(v.kappa) ? v.kappa : null };
}

function count(v: unknown): Count | null {
  return pair(v);
}

function video(v: unknown, i: number): SuiteAVideo {
  const where = `suites.suite_a.videos[${i}]`;
  if (!isObj(v)) throw new BenchmarksError(where, "must be an object");
  const f = (k: string) => at(v, k, where);
  const sil = f("silence").optObj();
  const pub = f("published").optObj();
  const rec = f("recall").optObj();
  const hal = f("hallucination").optObj();
  const dec = f("decoys").optObj();
  return {
    id: f("id").str(),
    subject: f("subject").optStr() ?? "",
    durationS: f("duration_s").num(),
    failed: typeof v.failed === "string" ? v.failed : v.failed ? String(v.failed) : null,
    silence: sil && isNum(sil.seconds) && isNum(sil.pauses) ? { seconds: sil.seconds, pauses: sil.pauses, pausesGe2s: isNum(sil.pauses_ge_2s) ? sil.pauses_ge_2s : 0 } : null,
    published: pub && isNum(pub.standard) && isNum(pub.extended) ? { standard: pub.standard, extended: pub.extended } : null,
    overlapS: f("overlap_s").optNum(),
    recallStandard: rec ? count(rec.standard) : null,
    recallWithExtended: rec ? count(rec.with_extended) : null,
    hallucinationClaims: hal ? count(hal.claims) : null,
    decoys: dec && Array.isArray(dec.stated) && isNum(dec.of) ? { stated: dec.stated.length, of: dec.of } : null,
    costModelUsd: f("cost_model_usd").optNum(),
    costTavilyUsd: f("cost_tavily_usd").optNum(),
    processingS: f("processing_s").optNum(),
  };
}

/** suite_a.json as the scorer writes it. */
export function parseSuiteA(raw: unknown): SuiteA {
  const where = "suites.suite_a";
  if (!isObj(raw)) throw new BenchmarksError(where, "must be an object");
  const f = (k: string) => at(raw, k, where);
  const pre = f("preregistration").obj();
  const agg = f("aggregate").obj();
  const a = (k: string) => at(agg, k, `${where}.aggregate`);
  const r = (k: string) => ratio(agg[k], `${where}.aggregate.${k}`);
  const or = (k: string) => optRatio(agg[k], `${where}.aggregate.${k}`);
  const cost = a("cost").obj();
  const proc = a("processing").obj();
  const sj = f("second_judge").optObj();
  const sjAgg = sj && isObj(sj.aggregate) ? sj.aggregate : {};
  const sjAgr = sj && isObj(sj.agreement) ? sj.agreement : {};
  const judge = f("judge").optObj();
  const ro = f("run_options").optObj();
  const hw = f("hardware").optObj();
  const probe = f("judge_probe").optObj();
  const total = probe && isObj(probe.total) ? probe.total : null;
  const voiced = probe && isObj(probe.voiced) && isObj(probe.voiced.total) ? probe.voiced.total : null;
  const counts: Record<string, number> = {};
  const cc = a("cue_counts").optObj();
  if (cc) for (const [k, v] of Object.entries(cc)) if (isNum(v)) counts[k] = v;
  const videos = Array.isArray(raw.videos) ? raw.videos.map(video) : [];

  return {
    complete: raw.complete === true,
    note: f("note").optStr(),
    date: f("date").str(),
    gitCommit: f("git_commit").str(),
    gitDirty: f("git_dirty").optBool(),
    preregistration: {
      tag: at(pre, "tag", `${where}.preregistration`).str(),
      commit: at(pre, "commit", `${where}.preregistration`).str(),
      committed: at(pre, "committed", `${where}.preregistration`).str(),
    },
    keySha256: f("key_sha256").str(),
    callsAfterPreregistration: f("ledger_calls_after_preregistration").optBool(),
    judgeModel: f("judge_model").optStr(),
    judgeNote: judge && typeof judge.note === "string" ? judge.note : null,
    secondJudge:
      sj && typeof sj.model === "string"
        ? {
            model: sj.model,
            recall: optRatio(sjAgg.recall, `${where}.second_judge.aggregate.recall`),
            hallucination: optRatio(sjAgg.hallucination, `${where}.second_judge.aggregate.hallucination`),
            hallucinationVoicedText: optRatio(sjAgg.hallucination_voiced_text, `${where}.second_judge.aggregate.hallucination_voiced_text`),
            agreement: { facts: agreementOf(sjAgr.facts), claims: agreementOf(sjAgr.claims), voicedCues: agreementOf(sjAgr.voiced_cues) },
          }
        : null,
    engineModels: f("engine_models").strings(),
    runOptions: ro
      ? {
          mode: typeof ro.mode === "string" ? ro.mode : null,
          profile: typeof ro.profile === "string" ? ro.profile : null,
          voice: typeof ro.voice === "string" ? ro.voice : null,
          research: typeof ro.research === "boolean" ? ro.research : null,
        }
      : null,
    hardware: hw
      ? {
          os: typeof hw.os === "string" ? hw.os : null,
          cpu: typeof hw.cpu === "string" ? hw.cpu : null,
          gpus: Array.isArray(hw.gpus) ? hw.gpus.filter((g): g is string => typeof g === "string") : [],
          speechServer: typeof hw.speech_server === "string" ? hw.speech_server : null,
          models: typeof hw.models === "string" ? hw.models : null,
        }
      : null,
    spendUsd: f("spend_usd").optNum(),
    aggregate: {
      videos: a("videos").num(),
      failedRuns: a("failed_runs").strings(),
      minutes: a("minutes").num(),
      silenceS: a("silence_s").optNum(),
      pauses: a("pauses").optNum(),
      pausesGe2s: a("pauses_ge_2s").optNum(),
      overlapSTotal: a("overlap_s_total").num(),
      extendedFreezeInSpeech: a("extended_freeze_in_speech").optNum(),
      capacityViolations: a("capacity_violations").optNum(),
      recallStandard: r("recall_standard"),
      recallWithExtended: r("recall_with_extended"),
      timelyRecallWithExtended: or("timely_recall_with_extended"),
      hallucinationClaims: r("hallucination_claims"),
      hallucinationCues: or("hallucination_cues"),
      redundancyCues: r("redundancy_cues"),
      spokenFactsRestated: or("spoken_facts_restated"),
      decoysStated: a("decoys_stated").num(),
      decoysTotal: a("decoys_total").num(),
      decoyNumberHits: a("decoy_number_hits").optNum(),
      recallTieAwareStandard: or("recall_tie_aware_standard"),
      recallTieAwareWithExtended: or("recall_tie_aware_with_extended"),
      hallucinationVoicedText: or("hallucination_voiced_text"),
      voicedUnverifiable: or("voiced_unverifiable"),
      voicedCuesWithUnsupported: or("voiced_cues_with_unsupported"),
      claimsNotInCueText: or("claims_not_in_cue_text"),
      hallucinationClaimsInCueText: or("hallucination_claims_in_cue_text"),
      decoysAbsentPart: a("decoys_absent_part").optNum(),
      cost: {
        modelUsd: at(cost, "model_usd", `${where}.aggregate.cost`).num(),
        tavilyUsd: at(cost, "tavily_usd", `${where}.aggregate.cost`).optNum() ?? 0,
        modelUsdPerMin: at(cost, "model_usd_per_min", `${where}.aggregate.cost`).optNum(),
        tavilyUsdPerMin: at(cost, "tavily_usd_per_min", `${where}.aggregate.cost`).optNum(),
        perMinCi95: pair(cost.per_min_ci95),
      },
      processing: {
        seconds: at(proc, "seconds", `${where}.aggregate.processing`).num(),
        sPerMin: at(proc, "s_per_min", `${where}.aggregate.processing`).optNum(),
        sPerMinCi95: pair(proc.s_per_min_ci95),
      },
      cueCounts: counts,
    },
    probe: total
      ? {
          facts: count(total.facts_credited_to_own_cue),
          decoys: count(total.decoys_credited_to_own_cue),
          negativesNotCredited: count(total.negatives_not_credited),
          voicedSensitivity: voiced ? count(voiced.sensitivity) : null,
          voicedSpecificity: voiced ? count(voiced.specificity) : null,
        }
      : null,
    videos,
  };
}

/** The whole file. Throws BenchmarksError naming the first field that doesn't hold. */
export function parseBenchmarks(raw: unknown): Benchmarks {
  if (!isObj(raw)) throw new BenchmarksError("the file", "must be a JSON object");
  const f = (k: string) => at(raw, k, "");
  const suites = f("suites").obj();
  const failures = Array.isArray(raw.known_failures) ? raw.known_failures : null;
  if (!failures) throw new BenchmarksError(".known_failures", "must be a list of {title, detail}");
  return {
    generatedAt: f("generated_at").str(),
    gitCommit: f("git_commit").str(),
    fixture: raw.fixture === true,
    suiteA: suites.suite_a === undefined ? null : parseSuiteA(suites.suite_a),
    otherSuites: Object.keys(suites).filter((k) => k !== "suite_a" && suites[k] !== undefined && suites[k] !== null),
    knownFailures: failures.map((x, i) => {
      if (!isObj(x)) throw new BenchmarksError(`.known_failures[${i}]`, "must be an object");
      return { title: at(x, "title", `.known_failures[${i}]`).str(), detail: at(x, "detail", `.known_failures[${i}]`).str() };
    }),
  };
}
