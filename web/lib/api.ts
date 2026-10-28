import { resolveApiBase } from "./api-base";
import { SseParser, type SseMessage } from "./sse";

/**
 * The Lull API's address: NEXT_PUBLIC_API_URL, inlined at build time (a production build
 * without it fails; see next.config.ts). Development and tests fall back to a local API.
 */
export const API_BASE = resolveApiBase({ NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL, NODE_ENV: process.env.NODE_ENV });

/** What the live route takes (engine/src/lull/api/live.py). Checked here first so a refusal costs no upload. */
export const LIMITS = {
  maxBytes: 30 * 1024 * 1024,
  maxSeconds: 120,
  extensions: [".mp4", ".mov", ".webm", ".mkv"],
} as const;

export const MODES = ["standard", "extended"] as const;
export type Mode = (typeof MODES)[number];

/** The calibrated voices the API accepts (lull.voices DEFAULT_WPS). */
export const VOICES = ["Sofia", "Aria"] as const;

/** USD the API's spend check asks to have left before a run starts: $0.05 a minute + $0.02. */
export function budgetEstimate(durationSeconds: number): number {
  return (0.05 * Math.max(0, durationSeconds)) / 60 + 0.02;
}

/** The engine's stages, in the order they run. */
export const STAGES = ["listen", "watch", "research", "plan", "schedule", "finalize", "publish"] as const;
export type StageName = (typeof STAGES)[number];

export const CUE_STATUSES = ["verified", "held", "edited", "extended", "approved"] as const;
export type CueStatus = (typeof CUE_STATUSES)[number];

// -- events ------------------------------------------------------------------------------------

type Meta = { seq: number | null; ts: number | null; stage: string | null };

export type RunStartEvent = Meta & {
  type: "run";
  run_id: string;
  /** Seconds of video the run describes (the API's trimmed copy). */
  duration: number | null;
  options: { mode: string; voice: string } | null;
  /** The owner token for Studio edits; only the first stream of a run carries it. */
  edit_token: string | null;
  resumed: boolean;
};
export type StageStartEvent = Meta & { type: "stage_start"; stage: string };
export type StageEndEvent = Meta & {
  type: "stage_end";
  stage: string;
  seconds: number;
  cached: boolean;
  /** The stage's own counts (listen: words, gaps, windows, …). */
  info: Record<string, unknown>;
};
export type CallEvent = Meta & {
  type: "call";
  role: string;
  model: string;
  provider: string | null;
  tokens_in: number;
  tokens_cached: number;
  tokens_out: number;
  cost_usd: number;
  latency_s: number;
};
export type CueEvent = Meta & {
  type: "cue";
  id: string;
  status: CueStatus;
  text: string;
  start: number;
  audio_dur: number | null;
  note: string | null;
  cached: boolean;
};
/** `count`: how many things the warning is about (the events left undescribed), when it says. */
export type WarningEvent = Meta & { type: "warning"; kind: string; message: string; count: number | null };
export type RunErrorEvent = Meta & { type: "error"; kind: string; message: string; scope: string | null };
export type DoneEvent = Meta & { type: "done"; run_id: string | null; seconds: number | null; summary: RunSummary | null };

export type RunEvent =
  | RunStartEvent
  | StageStartEvent
  | StageEndEvent
  | CallEvent
  | CueEvent
  | WarningEvent
  | RunErrorEvent
  | DoneEvent;

export type WcagRow = { criterion: string; level: string; name: string; status: string; reasons: string[] };

/** The published summary the `done` event carries (the fields this site reads). */
export type RunSummary = {
  cues: Partial<Record<CueStatus, number>>;
  coverage: { ratio: number | null; described: number; salient: number } | null;
  overlapSeconds: number | null;
  held: { id: string; reason: string | null }[];
  costUsd: number | null;
  costPerMin: number | null;
  wcag: WcagRow[];
  extendedProduced: boolean | null;
};

export type Obj = Record<string, unknown>;
export const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
export const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
export const str = (v: unknown): string | null => (typeof v === "string" ? v : null);
const KNOWN_EVENTS: ReadonlySet<string> = new Set(["run", "stage_start", "stage_end", "call", "cue", "warning", "error", "done"]);
const META_KEYS = new Set(["type", "stage", "ts", "seq", "seconds", "cached"]);

function readSummary(raw: unknown): RunSummary | null {
  if (!isObj(raw)) return null;
  const cues: Partial<Record<CueStatus, number>> = {};
  if (isObj(raw.cues)) for (const s of CUE_STATUSES) if (num(raw.cues[s]) !== null) cues[s] = raw.cues[s] as number;
  const cov = isObj(raw.coverage) ? raw.coverage : null;
  const held = Array.isArray(raw.held)
    ? raw.held.filter(isObj).map((h) => ({ id: str(h.id) ?? "", reason: str(h.reason) }))
    : [];
  const wcag = Array.isArray(raw.wcag)
    ? raw.wcag.filter(isObj).map((w) => ({
        criterion: str(w.criterion) ?? "",
        level: str(w.level) ?? "",
        name: str(w.name) ?? "",
        status: str(w.status) ?? "",
        reasons: Array.isArray(w.reasons) ? w.reasons.filter((r): r is string => typeof r === "string") : [],
      }))
    : [];
  return {
    cues,
    coverage: cov
      ? { ratio: num(cov.ratio), described: num(cov.described) ?? 0, salient: num(cov.salient_events) ?? 0 }
      : null,
    overlapSeconds: num(raw.overlap_seconds),
    held,
    costUsd: num(raw.cost_usd),
    costPerMin: num(raw.cost_per_min),
    wcag,
    extendedProduced: typeof raw.extended_produced === "boolean" ? raw.extended_produced : null,
  };
}

/**
 * One stream event, checked and typed. The API sends flat JSON: the engine's fields plus
 * `type`, `stage`, `ts` and `seq`. Returns null for an unknown type or a malformed event, so a
 * newer API can add events without breaking the page.
 */
export function parseEvent(data: string, sseType = "message"): RunEvent | null {
  let raw: unknown;
  try {
    raw = JSON.parse(data);
  } catch {
    return null;
  }
  if (!isObj(raw)) return null;
  const type = str(raw.type) ?? sseType;
  if (!KNOWN_EVENTS.has(type)) return null;
  const meta: Meta = { seq: Number.isInteger(raw.seq) ? (raw.seq as number) : null, ts: num(raw.ts), stage: str(raw.stage) };

  switch (type) {
    case "run": {
      const id = str(raw.run_id);
      if (!id) return null;
      const o = isObj(raw.options) ? raw.options : null;
      return {
        ...meta,
        type,
        run_id: id,
        duration: num(raw.duration),
        options: o ? { mode: str(o.mode) ?? "standard", voice: str(o.voice) ?? "" } : null,
        edit_token: str(raw.edit_token),
        resumed: raw.resumed === true,
      };
    }
    case "stage_start":
      return meta.stage ? { ...meta, type, stage: meta.stage } : null;
    case "stage_end": {
      if (!meta.stage) return null;
      const info: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(raw)) if (!META_KEYS.has(k)) info[k] = v;
      return { ...meta, type, stage: meta.stage, seconds: num(raw.seconds) ?? 0, cached: raw.cached === true, info };
    }
    case "call": {
      const role = str(raw.role);
      const model = str(raw.model);
      if (!role || !model) return null;
      return {
        ...meta,
        type,
        role,
        model,
        provider: str(raw.provider),
        tokens_in: num(raw.tokens_in) ?? 0,
        tokens_cached: num(raw.tokens_cached) ?? 0,
        tokens_out: num(raw.tokens_out) ?? 0,
        cost_usd: num(raw.cost_usd) ?? 0,
        latency_s: num(raw.latency_s) ?? 0,
      };
    }
    case "cue": {
      const id = str(raw.id);
      const status = str(raw.status);
      const start = num(raw.start);
      if (!id || start === null || !status || !(CUE_STATUSES as readonly string[]).includes(status)) return null;
      return {
        ...meta,
        type,
        id,
        status: status as CueStatus,
        text: str(raw.text) ?? "",
        start,
        audio_dur: num(raw.audio_dur),
        note: str(raw.note),
        cached: raw.cached === true,
      };
    }
    case "warning":
      return {
        ...meta,
        type,
        kind: str(raw.kind) ?? "warning",
        message: str(raw.message) ?? "",
        count: Array.isArray(raw.event_ids) ? raw.event_ids.length : null,
      };
    case "error":
      return {
        ...meta,
        type,
        kind: str(raw.kind) ?? "Error",
        message: str(raw.message) ?? "The run stopped.",
        scope: str(raw.scope),
      };
    case "done":
      return { ...meta, type, run_id: str(raw.run_id), seconds: num(raw.seconds), summary: readSummary(raw.summary) };
    default:
      return null;
  }
}

// -- refusals ----------------------------------------------------------------------------------

/** Why the API (or the way to it) said no. The API's own codes, plus what the client can tell. */
export type RefusalCode =
  | "budget"
  | "busy"
  | "too_large"
  | "format"
  | "too_long"
  | "unreadable"
  | "rate_limited"
  | "uploads_busy"
  | "one_upload_at_a_time"
  | "timeout"
  | "invalid"
  | "sample"
  | "not_found"
  | "forbidden"
  | "read_only"
  | "not_ready"
  | "not_editable"
  | "lint"
  | "invalid_transition"
  | "overlap"
  | "not_voiced"
  | "overloaded"
  | "engine"
  | "publish_failed"
  | "fork_failed"
  | "unreachable"
  | "unavailable"
  | "server";

/** The refusal codes the API sends itself (live.py, studio.py). */
export const API_REFUSALS: ReadonlySet<string> = new Set([
  "budget",
  "busy",
  "too_large",
  "format",
  "too_long",
  "unreadable",
  "rate_limited",
  "uploads_busy",
  "one_upload_at_a_time",
  "timeout",
  "invalid",
  "sample",
  "not_found",
  // the studio's (engine/src/lull/api/studio.py)
  "forbidden",
  "read_only",
  "not_ready",
  "not_editable",
  "lint",
  "invalid_transition",
  "overlap",
  "not_voiced",
  "overloaded",
  "engine",
  "publish_failed",
  "fork_failed",
]);

const FALLBACK_MESSAGE: Record<RefusalCode, string> = {
  budget: "The live demo has used its model budget. Recorded runs still work.",
  busy: "Another description is running. Try again in a minute, or open a recorded run.",
  too_large: "This file is over 30 MB. Live descriptions take files up to 30 MB.",
  format: "Send an mp4, mov, webm or mkv video.",
  too_long: "Live descriptions take videos up to 120 s.",
  unreadable: "This file could not be read as a video.",
  rate_limited: "You have reached the limit of live descriptions for now.",
  uploads_busy: "Other videos are being uploaded right now. Try again in a few seconds.",
  one_upload_at_a_time: "Your previous upload is still being received. Wait for it to finish, then try again.",
  timeout: "The upload did not arrive in time. Try a smaller file or a faster connection.",
  invalid: "The request was not accepted.",
  sample: "There is no sample with that id.",
  not_found: "There is no run with that id.",
  forbidden: "Only the person who started this run can change it. Fork it to edit a copy.",
  read_only: "Recorded runs are read-only. Fork one to edit a copy of your own.",
  not_ready: "This run has not been published yet, so there is nothing to edit.",
  not_editable: "This run cannot be edited.",
  lint: "This description breaks a writing rule. Fix it and save again.",
  invalid_transition: "This line can’t be approved as it is. Edit it so it is checked again.",
  overlap: "Nothing was published: some descriptions would overlap speech, the edge of their pause or each other.",
  not_voiced: "Nothing was published: some lines have lost their audio. Edit them again.",
  overloaded: "The studio is busy with other changes. Try again in a few seconds.",
  engine: "The edit could not be checked.",
  publish_failed: "Publishing failed.",
  fork_failed: "The run could not be copied.",
  unreachable: "The live service is not answering.",
  unavailable: "The live service is not available right now.",
  server: "The live service could not complete the request.",
};

/** A writing-rule finding (the engine's lint): an error stops a save, a warning is advice. */
export type LintFinding = { rule: string; message: string; span: [number, number] | null; severity: "error" | "warning" };

/** What some refusals carry besides their words. */
export type RefusalDetail = {
  /** overlap, not_voiced: the cues that stopped publishing. */
  cueIds?: string[];
  /** lint: every finding on the text, and the text as the API checked it (after its autofix). */
  findings?: LintFinding[];
  text?: string;
  /** invalid_transition: the status the cue has. */
  cueStatus?: string;
};

export class ApiError extends Error {
  readonly status: number;
  readonly code: RefusalCode;
  /** Seconds to wait, from Retry-After; null when the API gave none (or it isn't readable). */
  readonly retryAfter: number | null;
  /** For a budget refusal: which cap ("run", "day", "total", "provider"). */
  readonly scope: string | null;
  readonly detail: RefusalDetail;
  /** The `error` code the API sent, or null when the code was guessed from the status. */
  readonly apiCode: string | null;
  /** The server's own words, when it gave any (its message, else a plain detail string). */
  readonly said: string | null;

  constructor(init: {
    status: number;
    code: RefusalCode;
    message?: string | null;
    retryAfter?: number | null;
    scope?: string | null;
    detail?: RefusalDetail;
    apiCode?: string | null;
    said?: string | null;
  }) {
    super(init.message || FALLBACK_MESSAGE[init.code]);
    this.name = "ApiError";
    this.status = init.status;
    this.code = init.code;
    this.retryAfter = init.retryAfter ?? null;
    this.scope = init.scope ?? null;
    this.detail = init.detail ?? {};
    this.apiCode = init.apiCode ?? null;
    this.said = init.said ?? null;
  }
}

/** The engine's findings, checked: anything that isn't a finding is left out. */
export function readFindings(raw: unknown): LintFinding[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter(isObj).flatMap((f) => {
    const rule = str(f.rule);
    if (!rule) return [];
    const span = Array.isArray(f.span) && f.span.length === 2 && f.span.every((n) => Number.isInteger(n)) ? (f.span as [number, number]) : null;
    return [{ rule, message: str(f.message) ?? "", span, severity: f.severity === "warning" ? ("warning" as const) : ("error" as const) }];
  });
}

function readDetail(b: Obj): RefusalDetail {
  const detail: RefusalDetail = {};
  if (Array.isArray(b.cue_ids)) detail.cueIds = b.cue_ids.filter((i): i is string => typeof i === "string");
  if (Array.isArray(b.findings)) detail.findings = readFindings(b.findings);
  if (typeof b.text === "string") detail.text = b.text;
  if (typeof b.cue_status === "string") detail.cueStatus = b.cue_status;
  return detail;
}

function codeForStatus(status: number): RefusalCode {
  switch (status) {
    case 400:
    case 422:
      return "invalid";
    case 402:
      return "budget";
    case 403:
      return "forbidden";
    case 404:
      return "not_found";
    case 408:
      return "timeout";
    case 409:
      return "busy";
    case 413:
      return "too_large";
    case 429:
      return "rate_limited";
    case 502:
    case 503:
    case 504:
      return "unavailable";
    default:
      return "server";
  }
}

/** Retry-After as seconds: delta-seconds or an HTTP date. */
export function parseRetryAfter(value: string | null, now = Date.now()): number | null {
  if (!value) return null;
  const v = value.trim();
  if (/^\d+$/.test(v)) return Number(v);
  const at = Date.parse(v);
  return Number.isNaN(at) ? null : Math.max(0, Math.ceil((at - now) / 1000));
}

/** A refused response as an ApiError: the API's code and plain-words message when it sent them. */
export async function toApiError(res: Response): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  const b = isObj(body) ? body : {};
  const given = str(b.error);
  const code = given && API_REFUSALS.has(given) ? (given as RefusalCode) : codeForStatus(res.status);
  const message = str(b.message)?.trim() || null;
  return new ApiError({
    status: res.status,
    code,
    message,
    retryAfter: parseRetryAfter(res.headers.get("retry-after")),
    scope: str(b.scope),
    detail: readDetail(b),
    apiCode: given,
    said: message ?? (str(b.detail)?.trim() || null),
  });
}

export function isAbort(e: unknown): boolean {
  return e instanceof DOMException ? e.name === "AbortError" : isObj(e) && (e as { name?: unknown }).name === "AbortError";
}

function abortError(signal?: AbortSignal): unknown {
  return signal?.reason ?? new DOMException("The operation was aborted.", "AbortError");
}

/** fetch, with "nothing answered" as an ApiError (code "unreachable"); an abort is rethrown as is. */
export async function request(url: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch (e) {
    if (init.signal?.aborted || isAbort(e)) throw e;
    throw new ApiError({ status: 0, code: "unreachable" });
  }
}

// -- reads -------------------------------------------------------------------------------------

export type Budget = { spentToday: number | null; capDay: number | null; spentTotal: number | null; capTotal: number | null };
export type Health = { ok: boolean; speech: boolean | null; liveBusy: boolean | null; budget: Budget | null };

/** GET /v1/health. Throws ApiError ("unreachable" when nothing answers). */
export async function health(signal?: AbortSignal): Promise<Health> {
  const res = await request(`${API_BASE}/v1/health`, { signal, cache: "no-store" });
  if (!res.ok) throw await toApiError(res);
  const raw: unknown = await res.json().catch(() => null);
  if (!isObj(raw)) throw new ApiError({ status: res.status, code: "server" });
  const b = isObj(raw.budget) ? raw.budget : null;
  return {
    ok: raw.ok === true,
    speech: typeof raw.speech === "boolean" ? raw.speech : null,
    liveBusy: typeof raw.live_busy === "boolean" ? raw.live_busy : null,
    budget: b
      ? { spentToday: num(b.spent_today), capDay: num(b.cap_day), spentTotal: num(b.spent_total), capTotal: num(b.cap_total) }
      : null,
  };
}

/**
 * Which spend cap would stop a run that needs `estimate` USD, judged from a health answer
 * (the API's own rule: a cap of 0 or less is no cap). Null when the run fits.
 */
export function capReached(budget: Budget | null, estimate: number): "day" | "total" | null {
  if (!budget) return null;
  const over = (spent: number | null, cap: number | null) => spent !== null && cap !== null && cap > 0 && spent + estimate > cap;
  if (over(budget.spentTotal, budget.capTotal)) return "total";
  if (over(budget.spentToday, budget.capDay)) return "day";
  return null;
}

/** GET /v1/samples: the sample clips the API can describe. */
export async function samples(signal?: AbortSignal): Promise<{ id: string }[]> {
  const res = await request(`${API_BASE}/v1/samples`, { signal, cache: "no-store" });
  if (!res.ok) throw await toApiError(res);
  const raw: unknown = await res.json().catch(() => null);
  if (!Array.isArray(raw)) return [];
  return raw.filter(isObj).flatMap((s) => (typeof s.id === "string" && s.id ? [{ id: s.id }] : []));
}

/** The run's report page, served by the API. */
export function reportUrl(runId: string): string {
  return `${API_BASE}/v1/runs/${encodeURIComponent(runId)}/files/report.html`;
}

// -- live runs ---------------------------------------------------------------------------------

export type RunInput = { sample: string } | { file: File };
export type RunOptions = { mode: Mode; voice: string };

/** How a stream ended: on a last event, closed without one, or cut off (worth following again). */
export type StreamEnd = { end: "done" | "error" | "closed" | "dropped"; runId: string | null; lastSeq: number };

/** Drops repeats (a reconnect may replay events already seen) and notes the run id and the ending. */
class Tracker {
  lastSeq = -1;
  runId: string | null = null;
  terminal: "done" | "error" | null = null;

  constructor(private readonly onEvent: (ev: RunEvent) => void) {}

  take(msg: SseMessage): void {
    const ev = parseEvent(msg.data, msg.event);
    if (!ev) return;
    if (ev.seq !== null) {
      if (ev.seq <= this.lastSeq) return;
      this.lastSeq = ev.seq;
    }
    if (ev.type === "run") this.runId = ev.run_id;
    if (ev.type === "done" || ev.type === "error") this.terminal = ev.type;
    this.onEvent(ev);
  }

  result(end: StreamEnd["end"]): StreamEnd {
    return { end, runId: this.runId, lastSeq: this.lastSeq };
  }
}

async function readStream(body: ReadableStream<Uint8Array>, tracker: Tracker, signal?: AbortSignal): Promise<StreamEnd> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser({ onMessage: (m) => tracker.take(m) });
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      parser.feed(decoder.decode(value, { stream: true }));
    }
    parser.feed(decoder.decode());
    parser.end();
    return tracker.result(tracker.terminal ?? "closed");
  } catch (e) {
    if (signal?.aborted || isAbort(e)) throw signal?.aborted ? abortError(signal) : e;
    return tracker.result(tracker.terminal ?? "dropped");
  } finally {
    reader.releaseLock();
  }
}

/**
 * POST /v1/runs and read the event stream as it arrives. A refusal (402, 409, 413, 422, 429,
 * 503) comes back as JSON before any stream and is thrown as an ApiError. Resolves when the
 * stream ends; "dropped" means the connection broke while the run may still be going
 * (follow it with `followRun`).
 */
export async function startRun(
  input: RunInput,
  opts: RunOptions,
  onEvent: (ev: RunEvent) => void,
  signal?: AbortSignal,
): Promise<StreamEnd> {
  const form = new FormData();
  if ("file" in input) form.append("file", input.file, input.file.name);
  else form.append("sample", input.sample);
  form.append("mode", opts.mode);
  form.append("voice", opts.voice);

  const res = await request(`${API_BASE}/v1/runs`, {
    method: "POST",
    body: form,
    signal,
    headers: { Accept: "text/event-stream" },
    cache: "no-store",
  });
  if (!res.ok) throw await toApiError(res);
  if (!res.body || !(res.headers.get("content-type") ?? "").includes("text/event-stream")) {
    throw new ApiError({ status: res.status, code: "server" });
  }
  return readStream(res.body, new Tracker(onEvent), signal);
}

async function fetchFollow(url: string, tracker: Tracker, signal?: AbortSignal, token?: string | null): Promise<StreamEnd> {
  const after = tracker.lastSeq >= 0 ? `?after=${tracker.lastSeq}` : "";
  const headers: Record<string, string> = { Accept: "text/event-stream" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await request(url + after, { signal, headers, cache: "no-store" });
  if (res.status === 204) return tracker.result(tracker.terminal ?? "closed"); // nothing after what was seen
  if (!res.ok) throw await toApiError(res);
  if (!res.body) throw new ApiError({ status: res.status, code: "server" });
  return readStream(res.body, tracker, signal);
}

const sleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    if (signal?.aborted) return reject(abortError(signal));
    const id = setTimeout(() => {
      signal?.removeEventListener("abort", stop);
      resolve();
    }, ms);
    const stop = () => {
      clearTimeout(id);
      reject(abortError(signal));
    };
    signal?.addEventListener("abort", stop, { once: true });
  });

/** Between reconnects of a followed stream read with fetch. */
const RECONNECT_MS = 1000;

/**
 * Follows with fetch, sending the owner's edit token: the API resumes a run that stopped
 * before it finished only for its owner, and EventSource can't send an Authorization header.
 * A cut-off stream is read again from the last event seen (?after=), up to MAX_RECONNECTS
 * times in a row without progress.
 */
async function followWithToken(url: string, tracker: Tracker, token: string, signal?: AbortSignal): Promise<StreamEnd> {
  let failures = 0;
  for (;;) {
    const seen = tracker.lastSeq;
    let end: StreamEnd;
    try {
      end = await fetchFollow(url, tracker, signal, token);
    } catch (e) {
      if (!(e instanceof ApiError) || e.code !== "unreachable") throw e;
      end = tracker.result("dropped");
    }
    if (end.end !== "dropped") return end;
    failures = tracker.lastSeq > seen ? 1 : failures + 1;
    if (failures >= MAX_RECONNECTS) return end;
    await sleep(RECONNECT_MS, signal);
  }
}

const STREAM_TYPES = ["run", "stage_start", "stage_end", "call", "cue", "warning", "done", "message"] as const;
/** Failed reconnects in a row before following gives up and asks the API why. */
const MAX_RECONNECTS = 3;

/**
 * GET /v1/runs/{id}/events: replays the run and follows it while it is still going. Uses
 * EventSource, so a broken connection is retried with Last-Event-ID and resumes where it
 * left off (repeats are dropped). When the stream closes for good without a last event, or
 * reconnecting keeps failing, the same URL is fetched once to learn why (a 404, 409 or 402
 * is thrown as an ApiError) or to read whatever is left.
 *
 * With the run's edit token (`opts.token`, the owner's), the stream is read with fetch and
 * the token sent as a Bearer: a run that stopped before it finished is resumed only for its
 * owner (403 otherwise). A finished run's replay and a run still going are open to anyone.
 */
export function followRun(
  id: string,
  onEvent: (ev: RunEvent) => void,
  signal?: AbortSignal,
  opts: { token?: string | null } = {},
): Promise<StreamEnd> {
  const url = `${API_BASE}/v1/runs/${encodeURIComponent(id)}/events`;
  const tracker = new Tracker(onEvent);
  if (opts.token) return followWithToken(url, tracker, opts.token, signal);
  const ES = typeof EventSource === "function" ? EventSource : null;
  if (!ES) return fetchFollow(url, tracker, signal);

  return new Promise<StreamEnd>((resolve, reject) => {
    if (signal?.aborted) return reject(abortError(signal));
    const es = new ES(url);
    let settled = false;
    let failures = 0;

    const settle = (then: () => void) => {
      if (settled) return;
      settled = true;
      es.close();
      signal?.removeEventListener("abort", onAbort);
      then();
    };
    const onAbort = () => settle(() => reject(abortError(signal)));
    signal?.addEventListener("abort", onAbort, { once: true });

    const onMessage = (e: MessageEvent<string>) => {
      failures = 0;
      tracker.take({ event: e.type, data: e.data, id: e.lastEventId || null });
      const terminal = tracker.terminal;
      if (terminal) settle(() => resolve(tracker.result(terminal)));
    };
    for (const type of STREAM_TYPES) es.addEventListener(type, onMessage as EventListener);

    // "error" is both the run's own last event (a MessageEvent with data) and the connection's
    es.addEventListener("error", (e) => {
      if (e instanceof MessageEvent && typeof e.data === "string") return onMessage(e);
      if (es.readyState !== ES.CLOSED && ++failures < MAX_RECONNECTS) return; // the browser retries
      settle(() => {
        fetchFollow(url, tracker, signal).then(resolve, reject);
      });
    });
  });
}

// -- forks -------------------------------------------------------------------------------------

export type Fork = { runId: string; editToken: string };

/**
 * POST /v1/runs/{id}/fork: a published run (a recorded one too) copied into a new live run
 * that the caller owns, with its edit token. No model is called; forks are rate limited.
 * Throws ApiError with the API's own message (404 no such run, 409 still being described or
 * not published yet, 429 too many forks, 500 the copy failed).
 */
export async function forkRun(runId: string, signal?: AbortSignal): Promise<Fork> {
  const res = await request(`${API_BASE}/v1/runs/${encodeURIComponent(runId)}/fork`, { method: "POST", signal, cache: "no-store" });
  if (!res.ok) throw await toApiError(res);
  const raw: unknown = await res.json().catch(() => null);
  const id = isObj(raw) ? str(raw.run_id) : null;
  const token = isObj(raw) ? str(raw.edit_token) : null;
  if (!id || !token) throw new ApiError({ status: res.status, code: "server", message: "The copy was made, but the answer didn’t say where it is." });
  return { runId: id, editToken: token };
}

// -- the owner's edit token --------------------------------------------------------------------

const TOKEN_PREFIX = "lull-edit-token:";

/**
 * Keeps a live run's edit token for the Studio, keyed by run id. Storage may be unavailable
 * (private mode, blocked): then it returns false, and the run can be watched but not edited
 * from this browser.
 */
export function saveEditToken(runId: string, token: string): boolean {
  try {
    window.localStorage.setItem(TOKEN_PREFIX + runId, token);
    return window.localStorage.getItem(TOKEN_PREFIX + runId) === token;
  } catch {
    return false;
  }
}

export function readEditToken(runId: string): string | null {
  try {
    return window.localStorage.getItem(TOKEN_PREFIX + runId);
  } catch {
    return null;
  }
}

/** The run ids this browser holds an edit token for. */
export function editTokenRuns(): string[] {
  try {
    const out: string[] = [];
    for (let i = 0; i < window.localStorage.length; i++) {
      const key = window.localStorage.key(i);
      if (key?.startsWith(TOKEN_PREFIX)) out.push(key.slice(TOKEN_PREFIX.length));
    }
    return out.sort();
  } catch {
    return [];
  }
}

const FORK_PREFIX = "lull-fork:";

/** What this browser remembers about a copy it made: the run it was copied from, and when. */
export type ForkRecord = { source: string; made: number };

/** Notes a fork, so the studio can say which run it copies and when the API will remove it. */
export function saveForkRecord(runId: string, record: ForkRecord): void {
  try {
    window.localStorage.setItem(FORK_PREFIX + runId, JSON.stringify(record));
  } catch {
    // storage blocked: the studio then says copies expire without a time
  }
}

export function readForkRecord(runId: string): ForkRecord | null {
  try {
    const raw: unknown = JSON.parse(window.localStorage.getItem(FORK_PREFIX + runId) ?? "null");
    if (isObj(raw) && typeof raw.source === "string" && typeof raw.made === "number" && Number.isFinite(raw.made)) {
      return { source: raw.source, made: raw.made };
    }
  } catch {
    // unreadable: treated as unknown
  }
  return null;
}

/** The API removes a fork a day after it was made (studio.FORK_TTL_S). */
export const FORK_TTL_MS = 24 * 3600 * 1000;
