/**
 * The API's run reads and the studio's writes (engine/src/lull/api/app.py, store.py, studio.py):
 * one detail per run (status, source, timeline, loudness, the original video, and once it is
 * done its manifest and cues), its files, the run list, and the cue edits that need the owner's
 * edit token.
 */
import {
  API_BASE,
  ApiError,
  CUE_STATUSES,
  isAbort,
  isObj,
  num,
  readFindings,
  request,
  str,
  toApiError,
  type CueStatus,
  type LintFinding,
  type Obj,
} from "./api";
import { routePath } from "./api-routes";
import { SseParser } from "./sse";

export type Verdict = "supported" | "not_visible" | "contradicted";
const VERDICTS: readonly Verdict[] = ["supported", "not_visible", "contradicted"];

/** One claim of a line and what the frames said about it. `at`: the frame time it was judged at. */
export type ApiClaim = { text: string; verdict: Verdict; at: number | null };

/** A cue as the API gives it (store.cue_view): its take as a URL, never a path. */
export type ApiCue = {
  id: string;
  gapId: string | null;
  start: number;
  end: number | null;
  text: string;
  eventIds: string[];
  status: CueStatus;
  ahead: boolean;
  claims: ApiClaim[];
  audioUrl: string | null;
  audioDur: number | null;
  tempo: number | null;
  findings: LintFinding[];
  note: string | null;
  /** The picture holds while it plays (the API marks every cue). */
  extended: boolean;
};

export type WcagStatus = { criterion: string; level: string; name: string; status: string; via: string; reasons: string[] };

/** One role and model's calls. A web-search row counts `searches` and is never priced. */
export type CallGroup = { role: string; model: string; calls: number; costUsd: number | null; searches: number | null };

/** The run's published summary (publish.py `summary`), the fields the site reads. Missing fields are null. */
export type ManifestSummary = {
  cues: Partial<Record<CueStatus, number>>;
  coverage: {
    important: number | null;
    described: number | null;
    ratio: number | null;
    describedWithExtended: number | null;
    ratioWithExtended: number | null;
    undescribed: string[];
  } | null;
  /** Events the plan judged repetitive (rated 1-2), not counted as undescribed. Ids only in older summaries. */
  repetitive: { id: string; text: string | null }[] | null;
  overlap: { speech: number | null; betweenCues: number | null; outsidePauses: number | null; total: number | null } | null;
  overlapSeconds: number | null;
  held: { id: string; reason: string | null }[] | null;
  f113: { gapId: string; start: number; end: number; seconds: number }[] | null;
  lintWarnings: { cueId: string; rule: string; message: string }[] | null;
  costUsd: number | null;
  costPerMin: number | null;
  /** Model calls in USD (this run, and every run under its id), and web searches as a count: never priced. */
  spend: { modelUsd: number | null; modelUsdAllRuns: number | null; searches: number | null } | null;
  calls: CallGroup[] | null;
  wcag: WcagStatus[] | null;
  reviewed: boolean | null;
  extendedProduced: boolean | null;
  ttsDisclosure: string | null;
};

export type RunManifest = {
  runId: string | null;
  created: string | null;
  video: { name: string | null; duration: number | null };
  options: { mode: string | null; voice: string | null; language: string | null };
  outputs: Record<string, { bytes: number | null }>;
  summary: ManifestSummary;
  versions: { engine: string | null; models: string[]; tts: string | null };
};

/**
 * Where a run came from (facts.run_source): one of the API's samples (`sampleId`, null when a
 * recorded run's clip isn't one of them), an upload, or a fork (`forkedFrom` the run it
 * copies, `origin` what that came from in the end, `sampleId` when it was a sample).
 */
export type RunSource = {
  kind: "sample" | "upload" | "fork";
  sampleId: string | null;
  forkedFrom: string | null;
  origin: "sample" | "upload" | null;
};

/** Where a run stands: being described, done (published), failed, or stopped unfinished (its owner may resume it). */
export type RunState = "running" | "done" | "failed" | "stopped";
const STATES: readonly RunState[] = ["running", "done", "failed", "stopped"];

export type RunGapWindow = { id: string; start: number; end: number; capacity: number };

/**
 * GET /v1/runs/{id}. Anyone gets a done run in full; an unfinished run is shown in full only
 * to its owner (the edit token as a Bearer), and to anyone else as its id, status, kind of
 * source and time (`full` false). `manifest` and the cues exist once it is done. `speech` and
 * `gaps` (usable windows, capacity in words) come from its published cues.json; `loudness` is
 * dBFS per `loudnessHop` seconds, null when the API kept none; `original` is the described
 * video's address, absolute, null until done. `dirty`: changed in the studio since published.
 */
export type RunDetail = {
  id: string;
  kind: "showcase" | "live";
  status: RunState;
  source: RunSource | null;
  title: string | null;
  duration: number | null;
  created: string | null;
  error: { type: string | null; message: string | null; stage: string | null; scope: string | null } | null;
  manifest: RunManifest | null;
  cues: ApiCue[];
  extended: ApiCue[];
  dirty: boolean;
  speech: [number, number][] | null;
  gaps: RunGapWindow[] | null;
  wps: number | null;
  loudness: number[] | null;
  loudnessHop: number | null;
  original: string | null;
  full: boolean;
};

/** A done run's detail: its manifest is there. */
export type DoneDetail = RunDetail & { manifest: RunManifest };
export const isDone = (d: RunDetail): d is DoneDetail => d.status === "done" && d.manifest !== null;

/** A detail that must be a done run's (read back after a change, or a recorded run's). */
export function doneOrThrow(d: RunDetail): DoneDetail {
  if (isDone(d)) return d;
  throw new ApiError({ status: 200, code: "server", message: "The run isn’t published, so it can’t be shown here. Reload the page to see where it stands." });
}

function readSource(raw: unknown): RunSource | null {
  if (!isObj(raw)) return null;
  const kind = str(raw.kind);
  const origin = raw.origin === "sample" || raw.origin === "upload" ? raw.origin : null;
  // an older API said {kind, id}: the sample's id, or for a fork the run it copies
  if (kind === "sample") return { kind, sampleId: str(raw.sample_id) ?? str(raw.id), forkedFrom: null, origin: null };
  if (kind === "fork") return { kind, sampleId: str(raw.sample_id), forkedFrom: str(raw.forked_from) ?? str(raw.id), origin };
  if (kind === "upload") return { kind, sampleId: null, forkedFrom: null, origin: null };
  return null;
}

const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);
const bool = (v: unknown): boolean | null => (typeof v === "boolean" ? v : null);

export function readCue(raw: unknown): ApiCue | null {
  if (!isObj(raw)) return null;
  const id = str(raw.id);
  const start = num(raw.start);
  const status = str(raw.status);
  if (!id || start === null || !status || !(CUE_STATUSES as readonly string[]).includes(status)) return null;
  const claims = Array.isArray(raw.claims)
    ? raw.claims.filter(isObj).flatMap((c) => {
        const text = str(c.text);
        const verdict = str(c.verdict);
        if (!text || !verdict || !(VERDICTS as readonly string[]).includes(verdict)) return [];
        return [{ text, verdict: verdict as Verdict, at: num(c.at) }];
      })
    : [];
  return {
    id,
    gapId: str(raw.gap_id),
    start,
    end: num(raw.end),
    text: str(raw.text) ?? "",
    eventIds: strings(raw.event_ids),
    status: status as CueStatus,
    ahead: raw.ahead === true,
    claims,
    audioUrl: str(raw.audio_url),
    audioDur: num(raw.audio_dur),
    tempo: num(raw.tempo),
    findings: readFindings(raw.findings),
    note: str(raw.note),
    extended: raw.extended === true,
  };
}

function readSummary(raw: unknown): ManifestSummary {
  const s: Obj = isObj(raw) ? raw : {};
  const cues: Partial<Record<CueStatus, number>> = {};
  if (isObj(s.cues)) for (const k of CUE_STATUSES) if (num(s.cues[k]) !== null) cues[k] = s.cues[k] as number;
  const cov = isObj(s.coverage) ? s.coverage : null;
  const important = cov ? (num(cov.important_events) ?? num(cov.salient_events)) : null;
  let repetitive: ManifestSummary["repetitive"] = null;
  if (Array.isArray(s.repetitive)) {
    repetitive = s.repetitive.filter(isObj).flatMap((e) => (str(e.id) ? [{ id: e.id as string, text: str(e.text) }] : []));
  } else if (cov && Array.isArray(cov.repetitive)) {
    repetitive = strings(cov.repetitive).map((id) => ({ id, text: null }));
  }
  const ov = isObj(s.overlap) ? s.overlap : null;
  return {
    cues,
    coverage: cov
      ? {
          important,
          described: num(cov.described),
          ratio: num(cov.ratio),
          describedWithExtended: num(cov.described_with_extended),
          ratioWithExtended: num(cov.ratio_with_extended),
          undescribed: strings(cov.undescribed),
        }
      : null,
    repetitive,
    overlap: ov
      ? { speech: num(ov.speech_s), betweenCues: num(ov.between_cues_s), outsidePauses: num(ov.outside_pauses_s), total: num(ov.total_s) }
      : null,
    overlapSeconds: num(s.overlap_seconds),
    held: Array.isArray(s.held) ? s.held.filter(isObj).flatMap((h) => (str(h.id) ? [{ id: h.id as string, reason: str(h.reason) }] : [])) : null,
    f113: Array.isArray(s.f113)
      ? s.f113.filter(isObj).flatMap((f) => {
          const start = num(f.start);
          const end = num(f.end);
          return start === null || end === null ? [] : [{ gapId: str(f.gap_id) ?? "", start, end, seconds: num(f.seconds) ?? end - start }];
        })
      : null,
    lintWarnings: Array.isArray(s.lint_warnings)
      ? s.lint_warnings.filter(isObj).flatMap((w) => (str(w.cue_id) && str(w.rule) ? [{ cueId: w.cue_id as string, rule: w.rule as string, message: str(w.message) ?? "" }] : []))
      : null,
    costUsd: num(s.cost_usd),
    costPerMin: num(s.cost_per_min),
    spend: isObj(s.spend) ? { modelUsd: num(s.spend.model_usd), modelUsdAllRuns: num(s.spend.model_usd_all_runs), searches: num(s.spend.searches) } : null,
    calls: Array.isArray(s.calls_by_role_model)
      ? s.calls_by_role_model.filter(isObj).flatMap((g) =>
          str(g.role) && str(g.model)
            ? [{ role: g.role as string, model: g.model as string, calls: num(g.calls) ?? 0, costUsd: num(g.cost_usd), searches: num(g.searches) }]
            : [],
        )
      : null,
    wcag: Array.isArray(s.wcag)
      ? s.wcag.filter(isObj).map((w) => ({
          criterion: str(w.criterion) ?? "",
          level: str(w.level) ?? "",
          name: str(w.name) ?? "",
          status: str(w.status) ?? "",
          via: str(w.via) ?? "",
          reasons: strings(w.reasons),
        }))
      : null,
    reviewed: bool(s.reviewed),
    extendedProduced: bool(s.extended_produced),
    ttsDisclosure: str(s.tts_disclosure),
  };
}

export function readManifest(raw: unknown): RunManifest {
  const m: Obj = isObj(raw) ? raw : {};
  const video = isObj(m.video) ? m.video : {};
  const options = isObj(m.options) ? m.options : {};
  const versions = isObj(m.versions) ? m.versions : {};
  const outputs: RunManifest["outputs"] = {};
  if (isObj(m.outputs)) for (const [name, info] of Object.entries(m.outputs)) outputs[name] = { bytes: isObj(info) ? num(info.bytes) : null };
  return {
    runId: str(m.run_id),
    created: str(m.created),
    video: { name: str(video.name), duration: num(video.duration) },
    options: { mode: str(options.mode), voice: str(options.voice), language: str(options.language) },
    outputs,
    summary: readSummary(m.summary),
    versions: { engine: str(versions.engine), models: strings(versions.models), tts: str(versions.tts) },
  };
}

function readSpans(raw: unknown): [number, number][] | null {
  if (!Array.isArray(raw)) return null;
  return raw.flatMap((sp): [number, number][] => {
    const [a, b] = Array.isArray(sp) ? [num(sp[0]), num(sp[1])] : isObj(sp) ? [num(sp.start), num(sp.end)] : [null, null];
    return a !== null && b !== null && b > a ? [[a, b]] : [];
  });
}

function readGaps(raw: unknown): RunGapWindow[] | null {
  if (!Array.isArray(raw)) return null;
  const gaps = raw.filter(isObj).flatMap((g) => {
    const id = str(g.id);
    const start = num(g.start);
    const end = num(g.end);
    const capacity = num(g.capacity);
    return id && start !== null && end !== null && end > start && capacity !== null ? [{ id, start, end, capacity }] : [];
  });
  return gaps.length ? gaps : null;
}

export function readDetail(raw: unknown): RunDetail | null {
  if (!isObj(raw)) return null;
  const id = str(raw.id) ?? (isObj(raw.manifest) ? str(raw.manifest.run_id) : null);
  const manifest = isObj(raw.manifest) ? readManifest(raw.manifest) : null;
  // an older API sent no status: a run with a manifest was published
  const status = (STATES as readonly string[]).includes(str(raw.status) ?? "") ? (raw.status as RunState) : manifest ? "done" : null;
  if (!id || !status) return null;
  const cues = isObj(raw.cues) ? raw.cues : {};
  const list = (v: unknown) => (Array.isArray(v) ? v.map(readCue).filter((c): c is ApiCue => c !== null) : []);
  const err = isObj(raw.error) ? raw.error : null;
  const loudness = Array.isArray(raw.loudness) ? raw.loudness.map(num).filter((v): v is number => v !== null) : null;
  const original = str(raw.original);
  return {
    id,
    kind: raw.kind === "showcase" ? "showcase" : "live",
    status,
    source: readSource(raw.source),
    title: str(raw.title) ?? manifest?.video.name ?? null,
    duration: num(raw.duration) ?? manifest?.video.duration ?? null,
    created: str(raw.created) ?? manifest?.created ?? null,
    error: err ? { type: str(err.type), message: str(err.message), stage: str(err.stage), scope: str(err.scope) } : null,
    manifest,
    cues: list(cues.cues),
    extended: list(cues.extended),
    dirty: raw.dirty === true,
    speech: readSpans(raw.speech),
    gaps: readGaps(raw.gaps),
    wps: num(raw.wps),
    loudness: loudness && loudness.length ? loudness : null,
    loudnessHop: num(raw.loudness_hop_s),
    original: original ? apiUrl(original) : null,
    // the minimal view of an unfinished run has no title, duration or cues
    full: "title" in raw || manifest !== null,
  };
}

/** A file of a run, as the API serves it (manifest outputs and keyframes only). */
export function runFileUrl(id: string, rel: string): string {
  return `${API_BASE}${routePath("file", { id, path: rel })}`;
}

/** An API address made absolute: the cue views carry their audio as a path on the API. */
export const apiUrl = (path: string) => (/^https?:\/\//.test(path) ? path : `${API_BASE}${path.startsWith("/") ? "" : "/"}${path}`);

/**
 * The player's pattern for a run's cue recordings: the API's audio route per cue id. `version`
 * changes after an edit, so a new take isn't served from the player's cache.
 */
export function cueAudioPattern(id: string, version: number): string {
  // the cue id stays a {id} placeholder: the player fills it in
  return `${API_BASE}${routePath("cueAudio", { id }).replace("{cue_id}", "{id}")}${version ? `?v=${version}` : ""}`;
}

/**
 * GET /v1/runs/{id}, for any run, with the owner's edit token when this browser holds it (an
 * unfinished run is shown in full only to its owner). Throws ApiError: not_found when there is
 * no run with that id.
 */
export async function getRun(id: string, opts: { token?: string | null; signal?: AbortSignal } | AbortSignal = {}): Promise<RunDetail> {
  const { token, signal } = opts instanceof AbortSignal ? { token: null, signal: opts } : opts;
  const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
  const res = await request(`${API_BASE}${routePath("run", { id })}`, { signal, headers, cache: "no-store" });
  if (!res.ok) throw await toApiError(res);
  const detail = readDetail(await res.json().catch(() => null));
  if (!detail) throw new ApiError({ status: res.status, code: "server", message: "The run’s answer couldn’t be read." });
  return detail;
}

export type RunListItem = { id: string; kind: "showcase" | "live"; title: string; status: RunState; created: string | null };

/** GET /v1/runs: the recorded runs and the most recent runs of the samples (uploads are never listed). */
export async function listRuns(signal?: AbortSignal): Promise<RunListItem[]> {
  const res = await request(`${API_BASE}${routePath("listRuns")}`, { signal, cache: "no-store" });
  if (!res.ok) throw await toApiError(res);
  const raw: unknown = await res.json().catch(() => null);
  if (!Array.isArray(raw)) return [];
  return raw.filter(isObj).flatMap((r) => {
    const id = str(r.id);
    const status = str(r.status);
    if (!id || !status || !(STATES as readonly string[]).includes(status)) return [];
    return [{ id, kind: r.kind === "showcase" ? "showcase" : "live", title: str(r.title) ?? id, status: status as RunState, created: str(r.created) }];
  });
}

/** Why a finished run stopped: the message of its last `error` event, from the open replay. */
export async function failureReason(id: string, signal?: AbortSignal): Promise<string | null> {
  const res = await request(`${API_BASE}${routePath("events", { id })}`, { signal, headers: { Accept: "text/event-stream" }, cache: "no-store" });
  if (!res.ok || !res.body) return null;
  let reason: string | null = null;
  const parser = new SseParser({
    onMessage: (m) => {
      if (m.event !== "error") return;
      try {
        const ev: unknown = JSON.parse(m.data);
        if (isObj(ev) && typeof ev.message === "string" && ev.message.trim()) reason = ev.message.trim();
      } catch {
        // not JSON: not the run's own error event
      }
    },
  });
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      parser.feed(decoder.decode(value, { stream: true }));
    }
    parser.feed(decoder.decode());
    parser.end();
  } catch (e) {
    if (isAbort(e)) throw e;
  } finally {
    reader.releaseLock();
  }
  return reason;
}

/** Where a run stands, for the pages that open it by id. */
export type RunLookup =
  | { kind: "done"; detail: DoneDetail }
  | { kind: "running" }
  | { kind: "stopped" }
  | { kind: "failed"; reason: string | null }
  | { kind: "missing" };

/**
 * A run's detail by its own status: done (with its detail), still being described, stopped
 * unfinished (its owner may resume it), failed (with why), or not there (never made, or
 * removed). `token`: the owner's, when this browser holds it. Throws an ApiError when the API
 * can't be asked (unreachable, unavailable).
 */
export async function lookupRun(id: string, opts: { token?: string | null; signal?: AbortSignal } | AbortSignal = {}): Promise<RunLookup> {
  const { token, signal } = opts instanceof AbortSignal ? { token: null, signal: opts } : opts;
  let detail: RunDetail;
  try {
    detail = await getRun(id, { token, signal });
  } catch (e) {
    if (e instanceof ApiError && e.code === "not_found") return { kind: "missing" };
    throw e;
  }
  if (isDone(detail)) return { kind: "done", detail };
  if (detail.status === "running") return { kind: "running" };
  if (detail.status === "stopped") return { kind: "stopped" };
  if (detail.status === "failed") {
    const said = detail.error?.message?.trim();
    // the detail names the error once the API has recorded it; else the run's own last event
    return { kind: "failed", reason: said || (await failureReason(id, signal).catch((e) => (isAbort(e) ? Promise.reject(e) : null))) };
  }
  // done, but its manifest didn't read: the API reports such a run as failed; treat it so
  return { kind: "failed", reason: null };
}

// -- studio writes -------------------------------------------------------------------------------

async function write(path: string, token: string, init: { method: "POST" | "PATCH"; body?: unknown }, signal?: AbortSignal): Promise<unknown> {
  const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
  if (init.body !== undefined) headers["Content-Type"] = "application/json";
  const res = await request(`${API_BASE}${path}`, {
    method: init.method,
    headers,
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
    signal,
    cache: "no-store",
  });
  if (!res.ok) throw await toApiError(res);
  return res.json().catch(() => null);
}


function cueOrThrow(raw: unknown): ApiCue {
  const cue = readCue(raw);
  if (!cue) throw new ApiError({ status: 200, code: "server", message: "The change was made, but the answer couldn’t be read. Reload the page to see it." });
  return cue;
}

/**
 * PATCH the cue's text: the API lints it first (an error finding refuses it with 422 `lint`,
 * the findings and its autofixed text in `detail`), then checks it against the frames, fits
 * and voices it. Resolves with the cue as stored: "edited", or "held" with the reason.
 */
export async function editCue(id: string, cueId: string, text: string, token: string, signal?: AbortSignal): Promise<ApiCue> {
  return cueOrThrow(await write(routePath("editCue", { id, cue_id: cueId }), token, { method: "PATCH", body: { text } }, signal));
}

/** Signs off a verified, edited or voiced extended cue (409 invalid_transition otherwise). */
export async function approveCue(id: string, cueId: string, token: string, signal?: AbortSignal): Promise<ApiCue> {
  return cueOrThrow(await write(routePath("approveCue", { id, cue_id: cueId }), token, { method: "POST" }, signal));
}

/** Holds a cue for review, with an optional reason (at most 300 characters). */
export async function holdCue(id: string, cueId: string, token: string, reason?: string, signal?: AbortSignal): Promise<ApiCue> {
  const body = reason?.trim() ? { reason: reason.trim() } : undefined;
  return cueOrThrow(await write(routePath("holdCue", { id, cue_id: cueId }), token, { method: "POST", body }, signal));
}

/**
 * Publishes the cues as they are now. The overlap gate is never bypassed: 409 `overlap` (or
 * `not_voiced`) names the cues in `detail.cueIds` and nothing is written.
 */
export async function publishRun(id: string, token: string, signal?: AbortSignal): Promise<DoneDetail> {
  const detail = readDetail(await write(routePath("publish", { id }), token, { method: "POST" }, signal));
  if (!detail || !isDone(detail)) throw new ApiError({ status: 200, code: "server", message: "The run was published, but the answer couldn’t be read. Reload the page to see it." });
  return detail;
}
