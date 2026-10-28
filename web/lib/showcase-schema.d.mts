/** Types for showcase-schema.mjs. */

export type CueStatus = "verified" | "approved" | "edited" | "held" | "extended";
export type Verdict = "supported" | "not_visible" | "contradicted";

export type ShowcaseGap = { id: string; start: number; end: number; capacity: number };
export type ShowcaseCue = {
  id: string;
  start: number;
  end: number;
  text: string;
  status: CueStatus;
  ahead: boolean;
  /** The picture holds while it plays (newer exports say so; older ones only by status). */
  extended?: boolean;
  /** Why a held line was held, in the verifier's or a reviewer's words. */
  note?: string | null;
};

/** What the run's report says (the export's `summary`); empty values when the run predates them. */
export type ShowcaseSummary = {
  counts: Partial<Record<CueStatus, number>>;
  coverage: {
    importantEvents: number | null;
    described: number | null;
    ratio: number | null;
    describedWithExtended: number | null;
    ratioWithExtended: number | null;
    undescribed: string[];
  };
  repetitive: { id: string; text: string | null }[];
  f113: { gapId: string; start: number; end: number; eventIds: string[] }[];
  overlapSeconds: number | null;
  wcag: { criterion: string; level: string; name: string; status: string; blockers: string[] }[];
  models: string[];
  spend: {
    modelUsdThisRun: number | null;
    webSearchesThisRun: number | null;
    /** Notional: what the searches would cost at list price; not charged. */
    webSearchListUsdThisRun: number | null;
    modelUsdAllRunsOfRunId: number | null;
  };
};
export type ShowcaseWord = { text: string; start: number; end: number };
export type VerifyExample = {
  /** The cue the draft became, when the export names it. */
  cueId?: string;
  /** The draft as it was first checked. */
  cue: string;
  claims: { text: string; verdict: Verdict }[];
};

/**
 * One recorded run, as the landing and the showcase pages read it: the engine's export
 * (`lull export-showcase`) in public/showcase/<id>/, with every path made a site URL.
 */
export type Showcase = {
  id: string;
  title: string;
  credit: string;
  /** Seconds. */
  duration: number;
  /** dBFS of the original audio per 100 ms, floor -120. */
  loudness: number[];
  /** Speech spans, seconds. */
  speech: [number, number][];
  /** Usable windows: the pauses, guards already taken off, and how many words fit. */
  gaps: ShowcaseGap[];
  /** The published cues, standard and extended; an extended cue starts at its freeze point. */
  cues: ShowcaseCue[];
  /** Transcript words. */
  words?: ShowcaseWord[];
  /** URLs under /showcase/<id>/: the original sound (the video's own track) and the described mix. */
  audio?: { original: string; described: string };
  /** URL of the folder holding each cue's own recording, <id>.m4a. */
  cueAudioBase?: string;
  /** One draft line and the verifier's ruling on each of its claims. */
  verifyExample?: VerifyExample;
  /** Model spend under the run's id, USD: the whole run, every call it made. */
  costUsd?: number;
  /** What the run's report says, when the export carries it. */
  summary?: ShowcaseSummary;
  /** Set on a placeholder that is not an engine export; a production build refuses it. */
  interim?: true;
};

/** One row of public/showcase/index.json. */
export type ShowcaseIndexEntry = {
  id: string;
  title: string;
  credit: string;
  duration: number;
  /** The lines the export lists. */
  cues: number;
  /** Lines held for review (the summary's count when the export doesn't list them). */
  held?: number;
  costUsd?: number;
};

export declare const STATUSES: readonly CueStatus[];
export declare const VERDICTS: readonly Verdict[];
export declare const SHOWCASE_ID: RegExp;
export declare const CUE_ID: RegExp;
export declare const LAYOUT: {
  readonly json: string;
  readonly video: string;
  readonly descriptions: string;
  readonly extended: string;
  readonly keyframes: string;
  readonly cueAudioExt: string;
};

export declare class ShowcaseError extends Error {
  constructor(id: string, where: string, what: string);
}

export declare function showcaseUrl(id: string, rel?: string): string;
export declare function parseShowcase(raw: unknown, id: string): Showcase;
export declare function requiredFiles(raw: Record<string, unknown>): string[];
export declare function indexEntry(s: Showcase): ShowcaseIndexEntry;
export declare function heldOf(s: Pick<Showcase, "cues" | "summary">): number | null;
export declare function parseIndex(raw: unknown): ShowcaseIndexEntry[];
