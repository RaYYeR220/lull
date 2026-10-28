/**
 * One shape for a run, whichever source it comes from: a recorded run shipped with the site
 * (public/showcase/<id>/showcase.json) or a run the API serves (GET /v1/runs/{id}, its
 * cues.json and its files). The watch, live-run and studio pages read only this. Every field
 * comes from the run's own data; what a source doesn't have is null and the pages leave it out.
 */
import type { CueStatus, LintFinding } from "./api";
import { cueAudioPattern, runFileUrl, type ApiClaim, type ApiCue, type CueFileTimeline, type ManifestSummary, type RunDetail } from "./api-runs";
import { LAYOUT, showcaseUrl } from "./showcase-schema.mjs";
import type { Showcase, ShowcaseIndexEntry, ShowcaseWord } from "./showcase-schema.mjs";
import { inferWps } from "./studio-budget";

export type RunGap = { id: string; start: number; end: number; capacity: number };

/** The pieces a run took from the recorded run of its clip. */
export type Borrowed = { from: string; title: string; loudness: boolean; speech: boolean; gaps: boolean };

/** Exactly which pieces were lent, in words: "The soundtrack levels and speech are from …". */
export function borrowedWords(b: Borrowed): string {
  const lent = [b.loudness && "soundtrack levels", b.speech && "speech", b.gaps && "pauses"].filter((x): x is string => !!x);
  const list = lent.length > 1 ? `${lent.slice(0, -1).join(", ")} and ${lent[lent.length - 1]}` : (lent[0] ?? "");
  const missing = [b.speech && "speech", b.gaps && "pauses"].filter((x): x is string => !!x);
  const why = missing.length ? `; this run’s own files don’t include its ${missing.join(" or ")}` : "";
  return `The ${list} ${lent.length === 1 && !lent[0]!.endsWith("s") ? "is" : "are"} from the recorded run of this clip, ${b.title}${why}.`;
}

export type RunCue = {
  id: string;
  kind: "standard" | "extended";
  /** 1-based, in time order within its kind. */
  n: number;
  /** "Cue 3" or "Extended 1". */
  label: string;
  start: number;
  /** Where the voiced line ends (start + its take), or the recorded end; null when it isn't voiced. */
  end: number | null;
  text: string;
  status: CueStatus;
  /** The pause the line was placed in (standard lines). */
  gap: RunGap | null;
  /** The claims the line was checked on and the verdicts; null when the source doesn't say. */
  claims: ApiClaim[] | null;
  /** "draft": the verdicts on the first draft (the recorded export keeps one such example). */
  claimsOf: "line" | "draft";
  findings: LintFinding[] | null;
  note: string | null;
  audioDur: number | null;
  /** The evidence frame, when the run keeps one. */
  frame: string | null;
};

export type RunFile = { href: string; name: string; kind: string; what: string; bytes: number | null };

export type RunMedia = {
  src: string;
  descriptions?: string;
  extended?: string;
  describedAudio?: string;
  cueAudioBase?: string;
  cueAudioExt: string;
  crossOrigin?: "anonymous";
  /** "described": the video's own sound already carries the descriptions (the API's described.mp4). */
  sound?: "described";
};

/** Which model did what for the run (one per role), and the voice. */
export type Provenance = { watch: string | null; plan: string | null; verify: string | null; research: string | null; voice: string | null };

export type RunData = {
  id: string;
  source: "showcase" | "api";
  title: string;
  credit: string | null;
  /** The file the run described. */
  file: string | null;
  duration: number;
  /** dBFS of the soundtrack per 100 ms. */
  loudness: number[] | null;
  speech: [number, number][] | null;
  gaps: RunGap[] | null;
  words: ShowcaseWord[] | null;
  /** What the recorded run of the same clip lent: always its soundtrack levels, and speech and pauses only where the run's own files lack them. */
  borrowed: Borrowed | null;
  cues: RunCue[];
  media: RunMedia | null;
  files: RunFile[];
  summary: ManifestSummary | null;
  costUsd: number | null;
  voice: string | null;
  /** The voice's words per second, worked back from the pauses' capacities. */
  wps: number | null;
  /** Changed in the studio since it was last published; null when the source can't say. */
  dirty: boolean | null;
  created: string | null;
  provenance: Provenance | null;
};

export const VOICED: ReadonlySet<CueStatus> = new Set(["verified", "edited", "approved"]);
export const REVIEWED: ReadonlySet<CueStatus> = new Set(["edited", "approved"]);

/** The pause that contains `start`. */
function gapAt(start: number, gaps: readonly RunGap[] | null): RunGap | null {
  return gaps?.find((g) => start >= g.start - 1e-6 && start < g.end) ?? null;
}

type Draft = Omit<RunCue, "n" | "label" | "kind"> & { extended: boolean };

/** Time order, each kind counted apart: "Cue N" and "Extended N", as every page names them. */
function number(drafts: Draft[]): RunCue[] {
  const count = { standard: 0, extended: 0 };
  return [...drafts]
    .sort((a, b) => a.start - b.start || a.id.localeCompare(b.id))
    .map(({ extended, ...c }) => {
      const kind = extended ? "extended" : "standard";
      const n = ++count[kind];
      return { ...c, kind, n, label: kind === "extended" ? `Extended ${n}` : `Cue ${n}`, gap: kind === "standard" ? c.gap : null };
    });
}

/** The site's copy of a recorded run's files. */
export function showcaseFiles(s: Pick<Showcase, "id" | "audio">, sizes: Record<string, number | null> = {}): RunFile[] {
  const media = showcaseMedia(s as Showcase);
  const rows: Omit<RunFile, "bytes">[] = [
    { href: media.descriptions!, name: LAYOUT.descriptions, kind: "WebVTT", what: "Every voiced line with its timing, as a descriptions track." },
    { href: media.extended!, name: LAYOUT.extended, kind: "WebVTT", what: "The extended lines, each with how long the picture holds." },
    ...(media.describedAudio
      ? [{ href: media.describedAudio, name: "described-audio.m4a", kind: "AAC audio", what: "The original sound with the lines mixed in, as its own track." }]
      : []),
    { href: media.src, name: LAYOUT.video, kind: "480p video", what: "The original clip and its sound, as this page plays it." },
  ];
  return rows.map((r) => ({ ...r, bytes: sizes[r.href] ?? null }));
}

/** What the player reads for a recorded run (the export's fixed layout, as site URLs). */
export function showcaseMedia(s: Pick<Showcase, "id" | "audio" | "cueAudioBase">): RunMedia {
  return {
    src: showcaseUrl(s.id, LAYOUT.video),
    descriptions: showcaseUrl(s.id, LAYOUT.descriptions),
    extended: showcaseUrl(s.id, LAYOUT.extended),
    describedAudio: s.audio?.described,
    cueAudioBase: s.cueAudioBase,
    cueAudioExt: LAYOUT.cueAudioExt,
  };
}

/**
 * A recorded run. `hasFrame` says whether its evidence frame for a cue was shipped (the build
 * checks the files); `sizes` gives file sizes by URL.
 */
export function fromShowcase(s: Showcase, opts: { hasFrame?: (url: string) => boolean; sizes?: Record<string, number | null> } = {}): RunData {
  const gaps: RunGap[] = s.gaps.map((g) => ({ id: g.id, start: g.start, end: g.end, capacity: g.capacity }));
  const draftOf = s.verifyExample?.cueId;
  const cues = number(
    s.cues.map((c) => {
      const frame = showcaseUrl(s.id, `${LAYOUT.keyframes}${c.id}.jpg`);
      const extended = c.status === "extended";
      return {
        id: c.id,
        extended,
        start: c.start,
        end: c.end,
        text: c.text,
        status: c.status,
        gap: extended ? null : gapAt(c.start, gaps),
        claims: draftOf === c.id && s.verifyExample ? s.verifyExample.claims.map((k) => ({ text: k.text, verdict: k.verdict, at: null })) : null,
        claimsOf: "draft" as const,
        findings: null,
        note: null,
        audioDur: VOICED.has(c.status) || extended ? c.end - c.start : null,
        frame: !opts.hasFrame || opts.hasFrame(frame) ? frame : null,
      };
    }),
  );
  const wps = inferWps(gaps)?.wps ?? null;
  return {
    id: s.id,
    source: "showcase",
    title: s.title,
    credit: s.credit,
    file: null,
    duration: s.duration,
    loudness: s.loudness,
    speech: s.speech,
    gaps,
    words: s.words ?? null,
    borrowed: null,
    cues,
    media: showcaseMedia(s),
    files: showcaseFiles(s, opts.sizes),
    summary: null,
    costUsd: s.costUsd ?? null,
    voice: null,
    wps,
    dirty: null,
    created: null,
    provenance: null,
  };
}

/** The files the API serves for a run, the useful ones, in a fixed order, as the manifest lists them. */
const API_FILES: { name: string; kind: string; what: string }[] = [
  { name: "descriptions.vtt", kind: "WebVTT", what: "Every voiced line with its timing, as a descriptions track." },
  { name: "extended.vtt", kind: "WebVTT", what: "The extended lines, each with how long the picture holds." },
  { name: "described-audio.m4a", kind: "AAC audio", what: "The original sound with the lines mixed in, as its own track." },
  { name: "described.mp4", kind: "Video", what: "The video with two sound tracks: described first, original second." },
  { name: "extended.mp4", kind: "Video", what: "The extended version: the picture holds while each extended line is said." },
  { name: "script.md", kind: "Markdown", what: "The description script: every line with its time, status, claims and notes." },
  { name: "report.html", kind: "HTML", what: "The conformance report: WCAG status, coverage, held lines, spend." },
  { name: "cues.json", kind: "JSON", what: "Every cue, held ones too, with its checks: the studio’s working copy." },
];

/**
 * The recorded run whose clip this run described, as the API says: a run of one of the samples
 * the site ships (source "sample"), or a copy of one of its recorded runs (source "fork").
 * Never guessed from the file's name: an upload is never matched to a recorded run.
 */
export function clipOf(detail: Pick<RunDetail, "source">, index: readonly ShowcaseIndexEntry[]): ShowcaseIndexEntry | null {
  const src = detail.source;
  if (!src || !src.id || (src.kind !== "sample" && src.kind !== "fork")) return null;
  return index.find((e) => e.id === src.id) ?? null;
}

function provenance(summary: ManifestSummary, tts: string | null): Provenance | null {
  if (!summary.calls?.length && !tts) return null;
  const top = (role: string) => {
    const groups = (summary.calls ?? []).filter((g) => g.role === role);
    return groups.length ? groups.reduce((a, b) => (b.calls > a.calls ? b : a)).model : null;
  };
  return { watch: top("watch"), plan: top("plan"), verify: top("verify"), research: top("research"), voice: tts };
}

/**
 * A run the API serves. `timeline`: the speech and pauses from its cues.json. `clip`: the
 * recorded run of the same clip, when there is one; it lends the soundtrack's loudness (the
 * same audio), and speech and pauses only when the run's own files lack them (said so in
 * `borrowed`). When `clip` is the run itself (a recorded run opened through the API), its
 * media and files are the site's copies.
 */
export function fromApi(
  id: string,
  detail: RunDetail,
  opts: {
    timeline?: CueFileTimeline;
    clip?: Showcase | null;
    clipFiles?: RunFile[];
    /** Bumped after an edit: the cue recordings are fetched afresh. */
    audioVersion?: number;
    /** Bumped after a publish: the published files are fetched afresh. */
    filesVersion?: number;
  } = {},
): RunData {
  const { manifest } = detail;
  const clip = opts.clip ?? null;
  const own = opts.timeline ?? { speech: null, gaps: null };
  const gaps: RunGap[] | null = own.gaps ?? (clip ? clip.gaps.map((g) => ({ id: g.id, start: g.start, end: g.end, capacity: g.capacity })) : null);
  const speech = own.speech ?? clip?.speech ?? null;
  const byId = new Map((gaps ?? []).map((g) => [g.id, g]));

  const draft = (c: ApiCue, extended: boolean): Draft => {
    const voiced = c.audioDur !== null && c.status !== "held";
    return {
      id: c.id,
      extended,
      start: c.start,
      end: voiced ? c.start + c.audioDur! : null,
      text: c.text,
      status: c.status,
      gap: extended ? null : ((c.gapId ? byId.get(c.gapId) : undefined) ?? gapAt(c.start, gaps)),
      claims: c.claims,
      claimsOf: "line",
      findings: c.findings,
      note: c.note,
      audioDur: c.audioDur,
      frame: runFileUrl(id, `keyframes/${c.id}.jpg`),
    };
  };
  const cues = number([...detail.cues.map((c) => draft(c, false)), ...detail.extended.map((c) => draft(c, true))]);
  const duration = manifest.video.duration ?? clip?.duration ?? Math.max(0, ...cues.map((c) => c.end ?? c.start));

  const itself = clip !== null && clip.id === id;
  const has = (name: string) => name in manifest.outputs;
  const published = (name: string) => (has(name) ? runFileUrl(id, name) + (opts.filesVersion ? `?v=${opts.filesVersion}` : "") : undefined);
  let media: RunMedia | null;
  if (itself) media = showcaseMedia(clip);
  else {
    const src = clip ? showcaseUrl(clip.id, LAYOUT.video) : (published("described.mp4") ?? null);
    media = src
      ? {
          src,
          descriptions: published("descriptions.vtt"),
          extended: published("extended.vtt"),
          describedAudio: published("described-audio.m4a"),
          cueAudioBase: cueAudioPattern(id, opts.audioVersion ?? 0),
          cueAudioExt: "wav",
          crossOrigin: "anonymous",
          sound: clip ? undefined : "described",
        }
      : null;
  }
  const files =
    itself && opts.clipFiles
      ? opts.clipFiles
      : API_FILES.filter((f) => has(f.name)).map((f) => ({ ...f, href: runFileUrl(id, f.name), bytes: manifest.outputs[f.name]?.bytes ?? null }));

  return {
    id,
    source: "api",
    title: clip?.title ?? manifest.video.name ?? id,
    credit: clip?.credit ?? null,
    file: manifest.video.name,
    duration,
    loudness: clip?.loudness ?? null,
    speech,
    gaps,
    words: clip?.words ?? null,
    borrowed: clip && !itself ? { from: clip.id, title: clip.title, loudness: true, speech: !own.speech, gaps: !own.gaps } : null,
    cues,
    media,
    files,
    summary: manifest.summary,
    costUsd: manifest.summary.costUsd,
    voice: manifest.options.voice,
    wps: gaps ? (inferWps(gaps)?.wps ?? null) : null,
    dirty: detail.dirty,
    created: manifest.created,
    provenance: provenance(manifest.summary, manifest.versions.tts),
  };
}

/** The voiced standard lines (the ones in the described soundtrack), in time order. */
export const voicedLines = (run: Pick<RunData, "cues">) => run.cues.filter((c) => c.kind === "standard" && VOICED.has(c.status));

/**
 * How a line fits: "11 of 13 words", or how long the video waits for an extended line. With
 * `borrowed`, the pause is the recorded run's (the run's own files don't have one), and says so.
 */
export function fitWords(c: RunCue, count: (text: string) => number, opts: { borrowed?: boolean } = {}): string {
  if (c.kind === "extended") return c.end !== null ? `video holds ${(c.end - c.start).toFixed(2)} s` : "extended";
  const n = count(c.text);
  if (c.gap) return `${n} of ${c.gap.capacity} words${opts.borrowed ? ", in the recorded run’s pause" : ""}`;
  return `${n} ${n === 1 ? "word" : "words"}`;
}
