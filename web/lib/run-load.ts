/**
 * Loads a run the API serves into the pages' shape (lib/run-data.ts), in the browser: its
 * detail, the speech and pauses from its cues.json, and the recorded run of the same clip
 * when there is one (it lends the soundtrack and the original video).
 */
import { isAbort } from "./api";
import { getCueFileTimeline, lookupRun, type RunDetail } from "./api-runs";
import { fromApi, sameClip, type RunData, type RunFile } from "./run-data";
import { LAYOUT, parseShowcase, showcaseUrl } from "./showcase-schema.mjs";
import type { Showcase, ShowcaseIndexEntry } from "./showcase-schema.mjs";

/** The site's copy of a recorded run, fetched from public/showcase/<id>/showcase.json; null when it can't be read. */
export async function fetchShowcase(id: string, signal?: AbortSignal): Promise<Showcase | null> {
  try {
    const res = await fetch(showcaseUrl(id, LAYOUT.json), { signal });
    if (!res.ok) return null;
    return parseShowcase(await res.json(), id);
  } catch (e) {
    if (isAbort(e)) throw e;
    return null;
  }
}

export type Loaded =
  | { kind: "published"; run: RunData; detail: RunDetail }
  | { kind: "running" }
  | { kind: "failed"; reason: string | null }
  | { kind: "missing" };

type LoadOpts = {
  /** The recorded runs the site ships (public/showcase/index.json). */
  index: readonly ShowcaseIndexEntry[];
  /** A recorded run already in hand (the studio's own, rendered on the server), and its files. */
  clip?: Showcase | null;
  clipFiles?: RunFile[];
  audioVersion?: number;
  filesVersion?: number;
  signal?: AbortSignal;
};

/** A published run's data, ready for the pages; built again from a fresh detail by `build`. */
export async function buildRun(id: string, detail: RunDetail, opts: LoadOpts): Promise<RunData> {
  const entry = sameClip(detail, opts.index) ?? (opts.clip && opts.clip.id === id ? opts.clip : null);
  const [timeline, clip] = await Promise.all([
    "cues.json" in detail.manifest.outputs ? getCueFileTimeline(id, opts.signal) : Promise.resolve({ speech: null, gaps: null }),
    entry ? (opts.clip?.id === entry.id ? Promise.resolve(opts.clip) : fetchShowcase(entry.id, opts.signal)) : Promise.resolve(null),
  ]);
  return fromApi(id, detail, { timeline, clip, clipFiles: opts.clipFiles, audioVersion: opts.audioVersion, filesVersion: opts.filesVersion });
}

/**
 * Where the run stands and, when it is published, its data. Throws an ApiError when the API
 * can't be asked.
 */
export async function loadRun(id: string, opts: LoadOpts): Promise<Loaded> {
  const found = await lookupRun(id, opts.signal);
  if (found.kind !== "published") return found;
  return { kind: "published", detail: found.detail, run: await buildRun(id, found.detail, opts) };
}
