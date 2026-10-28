import type { CueStatus } from "./api";
import { clock } from "./run-format";

export type CueNote = { n: number; extended: boolean; status: CueStatus; text: string; note: string | null; start: number };

export type Announcer = {
  /** The stage that just started. Only the latest one waiting to be said is said. */
  stage(message: string): void;
  /** A cue that was finalised. One is read in full; several that land together are counted. */
  cue(c: CueNote): void;
  /** Said at once, dropping whatever was waiting (the run ended, a refusal). */
  now(message: string): void;
  dispose(): void;
};

type Options = {
  /** Least time between two announcements, ms. */
  gap?: number;
  clock?: () => number;
  schedule?: (fn: () => void, ms: number) => unknown;
  cancel?: (handle: unknown) => void;
};

const VOICED: ReadonlySet<CueStatus> = new Set(["verified", "edited", "approved", "extended"]);

function cueName(c: CueNote): string {
  return c.extended ? `Extended line ${c.n}` : `Cue ${c.n}`;
}

function cueLine(c: CueNote): string {
  if (c.status === "held") return `${cueName(c)} held: ${c.note?.trim() || "kept back for review"}.`;
  const verb = c.extended ? "ready" : c.status;
  return `${cueName(c)} ${verb} at ${clock(c.start)}: ${c.text}`;
}

function cueSummary(cues: CueNote[]): string {
  const held = cues.filter((c) => c.status === "held").length;
  const voiced = cues.filter((c) => VOICED.has(c.status)).length;
  const parts = [`${voiced} verified`];
  if (held) parts.push(`${held} held`);
  return `${cues.length} lines checked: ${parts.join(", ")}.`;
}

/**
 * Progress for a polite live region without flooding it: at most one announcement per `gap`
 * ms. What arrives in between is merged: the latest stage, then one cue in full or a count
 * of several.
 */
export function createAnnouncer(write: (text: string) => void, opts: Options = {}): Announcer {
  const gap = opts.gap ?? 2000;
  const now = opts.clock ?? (() => Date.now());
  const schedule = opts.schedule ?? ((fn, ms) => setTimeout(fn, ms));
  const cancel = opts.cancel ?? ((h) => clearTimeout(h as ReturnType<typeof setTimeout>));

  let stage: string | null = null;
  let cues: CueNote[] = [];
  let last = Number.NEGATIVE_INFINITY;
  let timer: unknown = null;

  const say = (text: string) => {
    write(text);
    last = now();
  };

  const flush = () => {
    timer = null;
    const parts: string[] = [];
    if (stage) parts.push(stage);
    if (cues.length === 1) parts.push(cueLine(cues[0]!));
    else if (cues.length > 1) parts.push(cueSummary(cues));
    stage = null;
    cues = [];
    if (parts.length) say(parts.join(" "));
  };

  const request = () => {
    if (timer !== null) return;
    timer = schedule(flush, Math.max(0, last + gap - now()));
  };

  const stop = () => {
    if (timer !== null) cancel(timer);
    timer = null;
  };

  return {
    stage(message) {
      stage = message;
      request();
    },
    cue(c) {
      cues.push(c);
      request();
    },
    now(message) {
      stop();
      stage = null;
      cues = [];
      say(message);
    },
    dispose() {
      stop();
      stage = null;
      cues = [];
    },
  };
}
