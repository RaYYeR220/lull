/**
 * Loads a run the API serves into the pages' shape (lib/run-data.ts), in the browser: its
 * detail (status, timeline, loudness, original video, and once done its manifest and cues),
 * with the owner's edit token when this browser holds it, and the recorded run of the same
 * clip when there is one (it lends what the API keeps no copy of).
 */
import { isAbort, readEditToken } from "./api";
import { lookupRun, type DoneDetail } from "./api-runs";
import { clipOf, fromApi, type RunData, type RunFile } from "./run-data";
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
  | { kind: "done"; run: RunData; detail: DoneDetail }
  | { kind: "running" }
  | { kind: "stopped" }
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

/** A done run's data, ready for the pages; built again from a fresh detail by `build`. */
export async function buildRun(id: string, detail: DoneDetail, opts: LoadOpts): Promise<RunData> {
  // the recorded run itself (opened through the API), or the one this run is a copy or sample of
  const entry = opts.clip && opts.clip.id === id ? opts.clip : clipOf(detail, opts.index);
  const clip = entry ? (opts.clip?.id === entry.id ? opts.clip : await fetchShowcase(entry.id, opts.signal)) : null;
  return fromApi(id, detail, { clip, clipFiles: opts.clipFiles, audioVersion: opts.audioVersion, filesVersion: opts.filesVersion });
}

/**
 * Where the run stands and, when it is done, its data. Sends the edit token when this browser
 * holds it. Throws an ApiError when the API can't be asked.
 */
export async function loadRun(id: string, opts: LoadOpts): Promise<Loaded> {
  const found = await lookupRun(id, { token: readEditToken(id), signal: opts.signal });
  if (found.kind !== "done") return found;
  return { kind: "done", detail: found.detail, run: await buildRun(id, found.detail, opts) };
}
