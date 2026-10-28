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
    cost: {
      modelUsd: number;
      tavilyUsd: number;
      modelUsdPerMin: number | null;
      tavilyUsdPerMin: number | null;
      /** The interval of model and web searches together per minute (how the scorer pairs them). */
      perMinCi95: [number, number] | null;
      /** The model-only interval, when the scorer writes one. */
      modelPerMinCi95: [number, number] | null;
    };
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

/** Suite B's recall of one reference's facts: within the window or anywhere, standard track or with extended. */
export type SuiteBRecall = { facts: number; lines: number; standardWindow: Ratio; bothWindow: Ratio; standardAnywhere: Ratio; bothAnywhere: Ratio };

export type SuiteBReference = {
  /** pfeiffer, hunt, usgs_ad */
  id: string;
  recall: SuiteBRecall;
  /** Without the facts that still hold an unresolved term (USGS's ASR), when there are any. */
  withoutUnresolved: SuiteBRecall | null;
  flaggedFacts: number;
  secondJudge: { recall: SuiteBRecall | null; agreementWindow: Agreement | null; agreementAnywhere: Agreement | null } | null;
};

export type OverlapSide = { side: string; descriptionS: number; overlapS: number; share: number | null };

export type SuiteBVideo = {
  video: string;
  runId: string | null;
  judgeProblems: number;
  /** Against the reference transcript of the original (shown first). */
  againstReference: { sides: OverlapSide[]; freezePointsInSpeech: number | null } | null;
  /** Against the Lull run's own speech detection: 0 for Lull by construction. */
  ownSpans: { sides: OverlapSide[]; freezePointsInSpeech: number | null };
  /** USGS AD measured on its own (AD version's) timeline: not comparable with a standard track. */
  usgsOwnTimeline: { descriptionS: number; overlapS: number; share: number | null; narrationMissingS: number | null } | null;
  density: { minutes: number; sides: { side: string; words: number; perMin: number }[] } | null;
};

export type ProbeTotal = { model: string; sensitivity: Count | null; specificity: Count | null; crossCredits: number | null };

export type SuiteB = {
  complete: boolean;
  smoke: boolean;
  note: string | null;
  date: string;
  gitCommit: string;
  gitDirty: boolean | null;
  preregistration: { tag: string; commit: string; committed: string };
  firstPreregistrationTag: string | null;
  referenceSetSha256: string;
  callsAfterPreregistration: boolean | null;
  judgeModel: string | null;
  secondJudgeModel: string | null;
  judgePromptSha256: string | null;
  windowS: number | null;
  longBlockWindowS: number | null;
  spendUsd: number | null;
  runs: { video: string; runId: string; clip: [number, number] | null; full: boolean; engine: string | null }[];
  references: SuiteBReference[];
  /** Human against human: one describer's lines judged against the other's facts. */
  baseline: { id: string; recall: SuiteBRecall }[];
  probe: { primary: ProbeTotal | null; second: ProbeTotal | null };
  videos: SuiteBVideo[];
};

export type NegativeRow = { rows: number; falseAccept: Ratio; falseAcceptClaims: Ratio; trueAccept: Ratio };

export type NegativeControl = {
  complete: boolean;
  date: string;
  gitCommit: string;
  preregistration: { tag: string; commit: string; committed: string };
  verifier: { model: string; temperature: number | null } | null;
  spendUsd: number | null;
  headline: NegativeRow & { rules: string[]; upperBound: boolean; discriminativeFalseAccept: Ratio | null; suiteARows: number | null };
  byRule: ({ rule: string } & NegativeRow)[];
};

export type AblationRow = { metric: string; with: number; without: number; difference: number; ci95: [number, number] | null; videos: number | null };

export type Ablation = {
  complete: boolean;
  date: string;
  gitCommit: string;
  preregistration: { controlsTag: string | null; suiteATag: string | null };
  engineFingerprint: string | null;
  claimSplit: { with: number | null; without: number | null } | null;
  headline: string | null;
  spendUsd: number | null;
  comparison: AblationRow[];
};

export type Benchmarks = {
  generatedAt: string;
  gitCommit: string;
  /** Test data, never the published evaluation. */
  fixture: boolean;
  suiteA: SuiteA | null;
  suiteB: SuiteB | null;
  negative: NegativeControl | null;
  ablation: Ablation | null;
  /** Other suites present in the file that this page doesn't draw. */
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
        modelPerMinCi95: pair(cost.model_per_min_ci95),
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

function bRecall(v: unknown, where: string): SuiteBRecall {
  if (!isObj(v)) throw new BenchmarksError(where, "must be an object of recall shares");
  return {
    facts: at(v, "facts", where).num(),
    lines: at(v, "lines", where).optNum() ?? 0,
    standardWindow: ratio(v.standard_window, `${where}.standard_window`),
    bothWindow: ratio(v.both_window, `${where}.both_window`),
    standardAnywhere: ratio(v.standard_anywhere, `${where}.standard_anywhere`),
    bothAnywhere: ratio(v.both_anywhere, `${where}.both_anywhere`),
  };
}

const optBRecall = (v: unknown, where: string): SuiteBRecall | null => (isObj(v) ? bRecall(v, where) : null);

function side(name: string, v: unknown): OverlapSide | null {
  if (!isObj(v) || !isNum(v.description_s) || !isNum(v.overlap_s)) return null;
  return { side: name, descriptionS: v.description_s, overlapS: v.overlap_s, share: isNum(v.share) ? v.share : null };
}

const SIDES = ["lull_standard", "pfeiffer_vtt", "hunt_vtt", "hunt_wav"];

function overlapOf(o: Obj): { sides: OverlapSide[]; freezePointsInSpeech: number | null } {
  return {
    sides: SIDES.map((k) => side(k, o[k])).filter((x): x is OverlapSide => x !== null),
    freezePointsInSpeech: Array.isArray(o.lull_extended_freeze_points_in_speech) ? o.lull_extended_freeze_points_in_speech.length : null,
  };
}

function bVideo(v: unknown, i: number): SuiteBVideo {
  const where = `suites.suite_b.videos[${i}]`;
  if (!isObj(v)) throw new BenchmarksError(where, "must be an object");
  const o = at(v, "overlap", where).obj();
  const ref = isObj(o.supplementary_against_reference_asr) ? o.supplementary_against_reference_asr : null;
  const usgs = isObj(o.usgs_ad_own_timeline) ? o.usgs_ad_own_timeline : null;
  const d = isObj(v.density) ? v.density : null;
  return {
    video: at(v, "video", where).str(),
    runId: at(v, "run_id", where).optStr(),
    judgeProblems: Array.isArray(v.judge_problems) ? v.judge_problems.length : 0,
    againstReference: ref ? overlapOf(ref) : null,
    ownSpans: overlapOf(o),
    usgsOwnTimeline:
      usgs && isNum(usgs.description_s) && isNum(usgs.overlap_s)
        ? { descriptionS: usgs.description_s, overlapS: usgs.overlap_s, share: isNum(usgs.share) ? usgs.share : null, narrationMissingS: isNum(usgs.narration_missing_from_ad_version_s) ? usgs.narration_missing_from_ad_version_s : null }
        : null,
    density:
      d && isNum(d.minutes)
        ? {
            minutes: d.minutes,
            sides: Object.entries(d)
              .filter(([k, x]) => k !== "minutes" && isObj(x) && isNum(x.words) && isNum(x.per_min))
              .map(([k, x]) => ({ side: k, words: (x as Obj).words as number, perMin: (x as Obj).per_min as number })),
          }
        : null,
  };
}

function probeTotal(v: unknown): ProbeTotal | null {
  if (!isObj(v) || typeof v.model !== "string" || !isObj(v.total)) return null;
  return { model: v.model, sensitivity: count(v.total.sensitivity), specificity: count(v.total.specificity), crossCredits: isNum(v.total.cross_credits) ? v.total.cross_credits : null };
}

function prereg(v: unknown, where: string) {
  const pre = isObj(v) ? v : null;
  if (!pre) throw new BenchmarksError(where, "must be an object {tag, commit, committed}");
  return { tag: at(pre, "tag", where).str(), commit: at(pre, "commit", where).str(), committed: at(pre, "committed", where).str() };
}

/** suite_b as its scorer (suite_b/score.py) writes it. */
export function parseSuiteB(raw: unknown): SuiteB {
  const where = "suites.suite_b";
  if (!isObj(raw)) throw new BenchmarksError(where, "must be an object");
  const f = (k: string) => at(raw, k, where);
  const summary = f("summary").obj();
  const refs = at(summary, "references", `${where}.summary`).obj();
  const base = isObj(summary.baseline) ? summary.baseline : {};
  const probe = f("judge_probe").optObj();
  return {
    complete: raw.complete === true,
    smoke: raw.smoke === true,
    note: f("note").optStr(),
    date: f("date").str(),
    gitCommit: f("git_commit").str(),
    gitDirty: f("git_dirty").optBool(),
    preregistration: prereg(raw.preregistration, `${where}.preregistration`),
    firstPreregistrationTag: f("first_preregistration_tag").optStr(),
    referenceSetSha256: f("reference_set_sha256").str(),
    callsAfterPreregistration: f("ledger_calls_after_preregistration").optBool(),
    judgeModel: f("judge_model").optStr(),
    secondJudgeModel: f("second_judge_model").optStr(),
    judgePromptSha256: f("judge_prompt_sha256").optStr(),
    windowS: f("window_s").optNum(),
    longBlockWindowS: f("long_block_window_s").optNum(),
    spendUsd: f("spend_usd").optNum(),
    runs: (Array.isArray(raw.runs) ? raw.runs : []).filter(isObj).map((r, i) => ({
      video: at(r, "video", `${where}.runs[${i}]`).str(),
      runId: at(r, "run_id", `${where}.runs[${i}]`).str(),
      clip: pair(r.clip),
      full: r.full === true,
      engine: isObj(r.engine) && typeof r.engine.engine === "string" ? r.engine.engine : null,
    })),
    references: Object.entries(refs).map(([id, v]) => {
      const w = `${where}.summary.references.${id}`;
      if (!isObj(v)) throw new BenchmarksError(w, "must be an object");
      const sj = isObj(v.second_judge) ? v.second_judge : null;
      return {
        id,
        recall: bRecall(v.recall, `${w}.recall`),
        withoutUnresolved: optBRecall(v.recall_without_unresolved, `${w}.recall_without_unresolved`),
        flaggedFacts: isNum(v.flagged_facts) ? v.flagged_facts : Array.isArray(v.flagged_facts) ? v.flagged_facts.length : 0,
        secondJudge: sj
          ? { recall: optBRecall(sj.recall, `${w}.second_judge.recall`), agreementWindow: agreementOf(sj.agreement_window), agreementAnywhere: agreementOf(sj.agreement_anywhere) }
          : null,
      };
    }),
    baseline: Object.entries(base).map(([id, v]) => ({ id, recall: bRecall(v, `${where}.summary.baseline.${id}`) })),
    probe: { primary: probeTotal(probe?.primary), second: probeTotal(probe?.second) },
    videos: Array.isArray(raw.videos) ? raw.videos.map(bVideo) : [],
  };
}

function negRow(v: unknown, where: string): NegativeRow {
  if (!isObj(v)) throw new BenchmarksError(where, "must be an object");
  return {
    rows: at(v, "rows", where).num(),
    falseAccept: ratio(v.false_accept, `${where}.false_accept`),
    falseAcceptClaims: ratio(v.false_accept_claims, `${where}.false_accept_claims`),
    trueAccept: ratio(v.true_accept, `${where}.true_accept`),
  };
}

/** controls.negative as controls/negative.py writes it. */
export function parseNegative(raw: unknown): NegativeControl {
  const where = "suites.controls.negative";
  if (!isObj(raw)) throw new BenchmarksError(where, "must be an object");
  const f = (k: string) => at(raw, k, where);
  const metrics = f("metrics").obj();
  const head = at(metrics, "headline", `${where}.metrics`).obj();
  const by = isObj(metrics.by_rule) ? metrics.by_rule : {};
  const ver = f("verifier").optObj();
  return {
    complete: raw.complete === true,
    date: f("date").str(),
    gitCommit: f("git_commit").str(),
    preregistration: prereg(raw.preregistration, `${where}.preregistration`),
    verifier: ver && typeof ver.model === "string" ? { model: ver.model, temperature: isNum(ver.temperature) ? ver.temperature : null } : null,
    spendUsd: f("spend_usd").optNum(),
    headline: {
      ...negRow(head, `${where}.metrics.headline`),
      rules: Array.isArray(head.rules) ? head.rules.filter((r): r is string => typeof r === "string") : [],
      upperBound: head.upper_bound === true,
      discriminativeFalseAccept: optRatio(head.discriminative_false_accept, `${where}.metrics.headline.discriminative_false_accept`),
      suiteARows: isNum(head.suite_a_rows) ? head.suite_a_rows : null,
    },
    byRule: Object.entries(by).map(([rule, v]) => ({ rule, ...negRow(v, `${where}.metrics.by_rule.${rule}`) })),
  };
}

/** controls.ablation as controls/ablation.py writes it. */
export function parseAblation(raw: unknown): Ablation {
  const where = "suites.controls.ablation";
  if (!isObj(raw)) throw new BenchmarksError(where, "must be an object");
  const f = (k: string) => at(raw, k, where);
  const pre = f("preregistration").optObj();
  const tag = (k: string) => (pre && isObj(pre[k]) && typeof (pre[k] as Obj).tag === "string" ? ((pre[k] as Obj).tag as string) : null);
  const split = f("claim_split").optObj();
  const comparison = f("comparison").obj();
  return {
    complete: raw.complete === true,
    date: f("date").str(),
    gitCommit: f("git_commit").str(),
    preregistration: { controlsTag: tag("controls"), suiteATag: tag("suite_a") },
    engineFingerprint: f("engine_fingerprint").optStr(),
    claimSplit: split ? { with: isNum(split.with) ? split.with : null, without: isNum(split.without) ? split.without : null } : null,
    headline: f("headline").optStr(),
    spendUsd: f("spend_usd").optNum(),
    comparison: Object.entries(comparison).map(([metric, v]) => {
      const w = `${where}.comparison.${metric}`;
      if (!isObj(v)) throw new BenchmarksError(w, "must be an object");
      return {
        metric,
        with: at(v, "with", w).num(),
        without: at(v, "without", w).num(),
        difference: at(v, "difference", w).num(),
        ci95: pair(v.ci95),
        videos: isNum(v.videos) ? v.videos : null,
      };
    }),
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
    suiteB: suites.suite_b === undefined || suites.suite_b === null ? null : parseSuiteB(suites.suite_b),
    negative: isObj(suites.controls) && suites.controls.negative != null ? parseNegative(suites.controls.negative) : null,
    ablation: isObj(suites.controls) && suites.controls.ablation != null ? parseAblation(suites.controls.ablation) : null,
    otherSuites: Object.keys(suites).filter((k) => !["suite_a", "suite_b", "controls"].includes(k) && suites[k] !== undefined && suites[k] !== null),
    knownFailures: failures.map((x, i) => {
      if (!isObj(x)) throw new BenchmarksError(`.known_failures[${i}]`, "must be an object");
      return { title: at(x, "title", `.known_failures[${i}]`).str(), detail: at(x, "detail", `.known_failures[${i}]`).str() };
    }),
  };
}

/**
 * The spend per minute as the page shows it, each value with only its own interval: model
 * calls and web searches together (the scorer's per_min_ci95 is over that sum), then model
 * calls alone (with an interval only when the scorer wrote one for them), then web searches.
 */
export function costPerMinute(g: SuiteA["aggregate"]): {
  combined: number | null;
  combinedCi: [number, number] | null;
  modelOnly: number | null;
  modelOnlyCi: [number, number] | null;
  webSearches: number | null;
} {
  const m = g.cost.modelUsdPerMin;
  const t = g.cost.tavilyUsdPerMin;
  return {
    combined: m === null ? null : m + (t ?? 0),
    combinedCi: g.cost.perMinCi95,
    modelOnly: m,
    modelOnlyCi: g.cost.modelPerMinCi95,
    webSearches: t,
  };
}

/**
 * What the results say about when their model calls were made. The scorer's flag is false
 * both when a call came before the pre-registration and when the ledger has nothing for these
 * runs; the spend over the referenced runs (spend_usd) tells the two apart as far as the file
 * can: with no spend found, the page says only that.
 */
export function preregCalls(a: Pick<SuiteA, "callsAfterPreregistration" | "spendUsd">): string | null {
  if (a.callsAfterPreregistration === null) return null;
  if (a.callsAfterPreregistration) return "Every model call the results use came after the pre-registration commit.";
  if (a.spendUsd !== null && a.spendUsd > 0) return "Some model calls the results use came before the pre-registration commit.";
  return "No model spend for these results was found in the ledger, so when their calls were made can’t be checked.";
}
