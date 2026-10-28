/**
 * The API's run reads and the studio's writes (engine/src/lull/api/app.py, store.py, studio.py):
 * a published run's manifest and cues, its files, the run list, and the cue edits that need the
 * owner's edit token.
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
};

export type WcagStatus = { criterion: string; level: string; name: string; status: string; via: string; reasons: string[] };

export type CallGroup = { role: string; model: string; calls: number; costUsd: number | null };

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
  /** The API's split of the spend: model calls in USD, web searches in credits. */
  spend: { modelsUsd: number | null; tavilyCredits: number | null } | null;
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
 * Where a run came from, as the API records it (request.json `source`): "sample" (one of its
 * samples, `id` the sample's), "fork" (a copy, `id` the run it copies), "upload", "showcase".
 */
export type RunSource = { kind: string; id: string | null };

/**
 * GET /v1/runs/{id}: a published run. `dirty`: changed in the studio since it was last
 * published. `source`: null when the API doesn't say.
 */
export type RunDetail = { manifest: RunManifest; cues: ApiCue[]; extended: ApiCue[]; dirty: boolean; source: RunSource | null };

function readSource(raw: unknown): RunSource | null {
  if (!isObj(raw)) return null;
  const kind = str(raw.kind);
  return kind ? { kind, id: str(raw.id) } : null;
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
    spend: isObj(s.spend) ? { modelsUsd: num(s.spend.models_usd), tavilyCredits: num(s.spend.tavily_credits) } : null,
    calls: Array.isArray(s.calls_by_role_model)
      ? s.calls_by_role_model.filter(isObj).flatMap((g) =>
          str(g.role) && str(g.model) ? [{ role: g.role as string, model: g.model as string, calls: num(g.calls) ?? 0, costUsd: num(g.cost_usd) }] : [],
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

export function readDetail(raw: unknown): RunDetail | null {
  if (!isObj(raw) || !isObj(raw.manifest)) return null;
  const cues = isObj(raw.cues) ? raw.cues : {};
  const list = (v: unknown) => (Array.isArray(v) ? v.map(readCue).filter((c): c is ApiCue => c !== null) : []);
  return {
    manifest: readManifest(raw.manifest),
    cues: list(cues.cues),
    extended: list(cues.extended),
    dirty: raw.dirty === true,
    source: readSource(raw.source) ?? readSource(raw.manifest.source),
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

/** GET /v1/runs/{id}. Throws ApiError: not_found when there is no published run with that id. */
export async function getRun(id: string, signal?: AbortSignal): Promise<RunDetail> {
  const res = await request(`${API_BASE}${routePath("run", { id })}`, { signal, cache: "no-store" });
  if (!res.ok) throw await toApiError(res);
  const detail = readDetail(await res.json().catch(() => null));
  if (!detail) throw new ApiError({ status: res.status, code: "server", message: "The run’s answer couldn’t be read." });
  return detail;
}

/** The engine's speech spans and pause windows from the run's cues.json (newer engines write them). */
export type CueFileTimeline = {
  speech: [number, number][] | null;
  gaps: { id: string; start: number; end: number; capacity: number }[] | null;
};

export function readCueFileTimeline(raw: unknown): CueFileTimeline {
  if (!isObj(raw)) return { speech: null, gaps: null };
  const speech = Array.isArray(raw.speech)
    ? raw.speech.filter(isObj).flatMap((sp): [number, number][] => {
        const a = num(sp.start);
        const b = num(sp.end);
        return a !== null && b !== null && b > a ? [[a, b]] : [];
      })
    : null;
  const gaps = Array.isArray(raw.gaps)
    ? raw.gaps.filter(isObj).flatMap((g) => {
        const id = str(g.id);
        // the usable window: the pause with its guards taken off, as capacity was counted
        const start = num(g.usable_start) ?? num(g.start);
        const end = num(g.usable_end) ?? num(g.end);
        const capacity = num(g.capacity_words) ?? num(g.capacity);
        return id && start !== null && end !== null && end > start && capacity !== null ? [{ id, start, end, capacity }] : [];
      })
    : null;
  return { speech, gaps: gaps && gaps.length ? gaps : null };
}

/** The run's cues.json, for its speech and pauses; null fields when it has none (or it can't be read). */
export async function getCueFileTimeline(id: string, signal?: AbortSignal): Promise<CueFileTimeline> {
  try {
    const res = await request(runFileUrl(id, "cues.json"), { signal, cache: "no-store" });
    if (!res.ok) return { speech: null, gaps: null };
    return readCueFileTimeline(await res.json().catch(() => null));
  } catch (e) {
    if (isAbort(e)) throw e;
    return { speech: null, gaps: null };
  }
}

export type RunStatus = "published" | "running" | "failed";
export type RunListItem = { id: string; kind: "showcase" | "live"; title: string; status: RunStatus; created: string | null };

/** GET /v1/runs: the recorded runs and the most recent live ones. */
export async function listRuns(signal?: AbortSignal): Promise<RunListItem[]> {
  const res = await request(`${API_BASE}${routePath("listRuns")}`, { signal, cache: "no-store" });
  if (!res.ok) throw await toApiError(res);
  const raw: unknown = await res.json().catch(() => null);
  if (!Array.isArray(raw)) return [];
  return raw.filter(isObj).flatMap((r) => {
    const id = str(r.id);
    const status = str(r.status);
    if (!id || (status !== "published" && status !== "running" && status !== "failed")) return [];
    return [{ id, kind: r.kind === "showcase" ? "showcase" : "live", title: str(r.title) ?? id, status, created: str(r.created) }];
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
  | { kind: "published"; detail: RunDetail }
  | { kind: "running" }
  | { kind: "failed"; reason: string | null }
  | { kind: "missing" };

/**
 * A published run's detail, or else what the run list says about the id: still being
 * described, stopped (with its reason), or not there (never made, or removed). Throws an
 * ApiError when the API can't be asked (unreachable, unavailable).
 */
export async function lookupRun(id: string, signal?: AbortSignal): Promise<RunLookup> {
  try {
    return { kind: "published", detail: await getRun(id, signal) };
  } catch (e) {
    if (!(e instanceof ApiError) || e.code !== "not_found") throw e;
  }
  const entry = (await listRuns(signal)).find((r) => r.id === id);
  if (!entry) return { kind: "missing" };
  if (entry.status === "running") return { kind: "running" };
  if (entry.status === "failed") return { kind: "failed", reason: await failureReason(id, signal).catch((e) => (isAbort(e) ? Promise.reject(e) : null)) };
  // listed as published but its detail was not found a moment ago: it has just been removed
  return { kind: "missing" };
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
export async function publishRun(id: string, token: string, signal?: AbortSignal): Promise<RunDetail> {
  const detail = readDetail(await write(routePath("publish", { id }), token, { method: "POST" }, signal));
  if (!detail) throw new ApiError({ status: 200, code: "server", message: "The run was published, but the answer couldn’t be read. Reload the page to see it." });
  return detail;
}
