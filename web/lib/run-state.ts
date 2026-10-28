import { STAGES, type ApiError, type CallEvent, type CueEvent, type RunEvent, type RunSummary, type StageName } from "./api";

export type StageStatus = "waiting" | "active" | "done" | "failed";
export type StageView = { status: StageStatus; seconds: number | null; cached: boolean; info: Record<string, unknown> | null };

/** A cue as the page shows it: numbered within its kind in the order it was finalised. */
export type CueView = CueEvent & { n: number; extended: boolean };

/** What listen found: counts only (the stream carries no positions). */
export type Heard = { words: number | null; pauses: number | null; capacityWords: number | null; speechSeconds: number | null; hasAudio: boolean | null };

export type RunSource =
  | { kind: "sample"; id: string; title: string }
  | { kind: "file"; name: string; bytes: number }
  | { kind: "follow"; id: string };

export type RunState = {
  /** idle: choosing. sending: the upload is on its way, no event yet. lost: the stream broke and could not be followed. */
  phase: "idle" | "sending" | "running" | "done" | "error" | "lost";
  source: RunSource | null;
  /** A refusal answered before any stream (shown next to the picker). */
  refusal: ApiError | null;
  /** Why following the run failed, when phase is "lost". */
  lost: ApiError | null;
  runId: string | null;
  duration: number | null;
  options: { mode: string; voice: string } | null;
  resumed: boolean;
  stages: Record<StageName, StageView>;
  current: StageName | null;
  calls: CallEvent[];
  cost: number;
  tokensIn: number;
  tokensOut: number;
  cues: CueView[];
  /** The cue finalised last. */
  latestCue: string | null;
  warnings: { kind: string; message: string; count: number | null }[];
  heard: Heard | null;
  error: { kind: string; message: string; scope: string | null; stage: string | null } | null;
  summary: RunSummary | null;
  /** Server clock (s) of the first and the latest event, and the smallest client minus server gap seen. */
  firstTs: number | null;
  lastTs: number | null;
  clockOffset: number | null;
  /** Client clock (ms) when sending began; the elapsed time before the first event. */
  sentAt: number | null;
  lastSeq: number;
};

export type CueLabel = { n: number; extended: boolean };

export type RunAction =
  | { type: "reset" }
  | { type: "send"; source: RunSource; at: number }
  | { type: "follow"; runId: string; at: number }
  /** `label` numbers a cue event (the page numbers cues as it announces them); computed when absent. */
  | { type: "event"; ev: RunEvent; at: number; label?: CueLabel }
  | { type: "refused"; error: ApiError }
  | { type: "lost"; error: ApiError | null };

const isStage = (s: string | null): s is StageName => s !== null && (STAGES as readonly string[]).includes(s);

function freshStages(): Record<StageName, StageView> {
  return Object.fromEntries(STAGES.map((s) => [s, { status: "waiting", seconds: null, cached: false, info: null }])) as Record<
    StageName,
    StageView
  >;
}

export function initialRun(): RunState {
  return {
    phase: "idle",
    source: null,
    refusal: null,
    lost: null,
    runId: null,
    duration: null,
    options: null,
    resumed: false,
    stages: freshStages(),
    current: null,
    calls: [],
    cost: 0,
    tokensIn: 0,
    tokensOut: 0,
    cues: [],
    latestCue: null,
    warnings: [],
    heard: null,
    error: null,
    summary: null,
    firstTs: null,
    lastTs: null,
    clockOffset: null,
    sentAt: null,
    lastSeq: -1,
  };
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

function readHeard(info: Record<string, unknown>): Heard {
  return {
    words: num(info.words),
    pauses: num(info.windows) ?? num(info.gaps),
    capacityWords: num(info.capacity_words),
    speechSeconds: num(info.speech_s),
    hasAudio: typeof info.has_audio === "boolean" ? info.has_audio : null,
  };
}

const byStart = (a: CueView, b: CueView) => a.start - b.start || a.id.localeCompare(b.id);
/** Extended cues (the video waits for them) have no pause of their own; their ids start with "x". */
export const isExtendedCue = (c: Pick<CueEvent, "id" | "status">) => c.status === "extended" || /^x/.test(c.id);

/**
 * Numbers cues within their kind in the order they are finalised (standard cues come in time
 * order, then extended ones), so a number never changes once shown. A cue seen again keeps it.
 */
export class CueNumbers {
  private byId = new Map<string, CueLabel>();
  private standard = 0;
  private extended = 0;

  label(c: Pick<CueEvent, "id" | "status">): CueLabel {
    const known = this.byId.get(c.id);
    if (known) return known;
    const extended = isExtendedCue(c);
    const label = { n: extended ? ++this.extended : ++this.standard, extended };
    this.byId.set(c.id, label);
    return label;
  }
}

function withCue(cues: CueView[], ev: CueEvent, label: CueLabel | undefined): CueView[] {
  const i = cues.findIndex((c) => c.id === ev.id);
  if (i >= 0) {
    const next = cues.slice();
    next[i] = { ...ev, n: cues[i]!.n, extended: cues[i]!.extended };
    return next.sort(byStart);
  }
  const extended = label?.extended ?? isExtendedCue(ev);
  const n = label?.n ?? cues.filter((c) => c.extended === extended).length + 1;
  return [...cues, { ...ev, n, extended }].sort(byStart);
}

function onEvent(state: RunState, ev: RunEvent, at: number, label?: CueLabel): RunState {
  if (ev.seq !== null && ev.seq <= state.lastSeq) return state; // a replayed repeat
  const s: RunState = { ...state, lastSeq: ev.seq ?? state.lastSeq };
  if (ev.ts !== null) {
    s.firstTs ??= ev.ts;
    s.lastTs = ev.ts;
    const gap = at / 1000 - ev.ts;
    s.clockOffset = s.clockOffset === null ? gap : Math.min(s.clockOffset, gap);
  }

  switch (ev.type) {
    case "run":
      s.runId = ev.run_id;
      s.duration = ev.duration ?? s.duration;
      s.options = ev.options ?? s.options;
      if (s.phase === "idle" || s.phase === "sending" || s.phase === "lost") s.phase = "running";
      if (ev.resumed) {
        // a resumed attempt runs every stage again (cached ones in a moment)
        s.resumed = true;
        s.stages = freshStages();
        s.current = null;
      }
      return s;
    case "stage_start": {
      if (!isStage(ev.stage)) return s;
      s.stages = { ...s.stages, [ev.stage]: { status: "active", seconds: null, cached: false, info: null } };
      s.current = ev.stage;
      if (s.phase === "sending" || s.phase === "idle") s.phase = "running";
      return s;
    }
    case "stage_end": {
      if (!isStage(ev.stage)) return s;
      s.stages = { ...s.stages, [ev.stage]: { status: "done", seconds: ev.seconds, cached: ev.cached, info: ev.info } };
      if (ev.stage === "listen") s.heard = readHeard(ev.info);
      return s;
    }
    case "call":
      s.calls = [...s.calls, ev];
      s.cost += ev.cost_usd;
      s.tokensIn += ev.tokens_in;
      s.tokensOut += ev.tokens_out;
      return s;
    case "cue":
      s.cues = withCue(s.cues, ev, label);
      s.latestCue = ev.id;
      return s;
    case "warning":
      s.warnings = [...s.warnings, { kind: ev.kind, message: ev.message, count: ev.count }];
      return s;
    case "error": {
      const stage = isStage(ev.stage) ? ev.stage : s.current;
      if (stage && s.stages[stage].status === "active") {
        s.stages = { ...s.stages, [stage]: { ...s.stages[stage], status: "failed" } };
      }
      s.error = { kind: ev.kind, message: ev.message, scope: ev.scope, stage };
      s.phase = "error";
      return s;
    }
    case "done":
      s.summary = ev.summary;
      s.runId ??= ev.run_id;
      s.phase = "done";
      return s;
  }
}

export function runReducer(state: RunState, action: RunAction): RunState {
  switch (action.type) {
    case "reset":
      return initialRun();
    case "send":
      return { ...initialRun(), phase: "sending", source: action.source, sentAt: action.at };
    case "follow":
      // following the run already on screen (its stream broke): keep what was seen; the
      // replay's repeats are dropped by seq
      if (state.runId === action.runId) return { ...state, phase: "running", lost: null };
      return { ...initialRun(), phase: "running", source: { kind: "follow", id: action.runId }, runId: action.runId, sentAt: action.at };
    case "event":
      return onEvent(state, action.ev, action.at, action.label);
    case "refused":
      return { ...initialRun(), refusal: action.error, source: state.source };
    case "lost":
      return { ...state, phase: "lost", lost: action.error };
  }
}

/**
 * Seconds the run has been going at client time `now` (ms), on the server's clock: from its
 * first event to its last once it has ended, else to now corrected by the observed offset.
 */
export function elapsedSeconds(s: RunState, now: number): number {
  const ended = s.phase === "done" || s.phase === "error";
  if (s.firstTs !== null && s.lastTs !== null) {
    if (ended) return Math.max(0, s.lastTs - s.firstTs);
    const serverNow = now / 1000 - (s.clockOffset ?? 0);
    return Math.max(0, serverNow - s.firstTs);
  }
  return s.sentAt !== null ? Math.max(0, (now - s.sentAt) / 1000) : 0;
}

export type ModelTotal = { model: string; calls: number; tokensIn: number; tokensOut: number; cost: number; roles: string[]; search: boolean };

/** Calls grouped by model, in the order each model was first called. */
export function totalsByModel(calls: readonly CallEvent[]): ModelTotal[] {
  const out = new Map<string, ModelTotal>();
  for (const c of calls) {
    let t = out.get(c.model);
    if (!t) {
      t = { model: c.model, calls: 0, tokensIn: 0, tokensOut: 0, cost: 0, roles: [], search: c.search };
      out.set(c.model, t);
    }
    t.calls += 1;
    t.tokensIn += c.tokens_in;
    t.tokensOut += c.tokens_out;
    t.cost += c.cost_usd;
    if (!t.roles.includes(c.role)) t.roles.push(c.role);
  }
  return [...out.values()];
}
