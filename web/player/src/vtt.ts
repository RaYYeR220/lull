/**
 * A small, forgiving WebVTT reader for description tracks.
 *
 * It returns each cue's id, start, end and plain text (tags stripped, entities decoded,
 * lines joined), plus the pause from a `NOTE extended pause=<seconds>` block placed right
 * before an extended cue. It never throws: a block it can't read is skipped, and a file
 * without the WEBVTT signature is still read cue by cue. Header lines such as
 * `Kind: descriptions` and `Language: en` are kept.
 */
export type VttCue = {
  /** The cue identifier line, or "" when the cue has none. */
  id: string;
  /** Seconds. */
  start: number;
  /** Seconds; never before start. */
  end: number;
  /** One line of plain text. */
  text: string;
  /** Seconds the programme waits for an extended cue, from its NOTE line. */
  pause?: number;
};

export type VttTrack = {
  cues: VttCue[];
  kind?: string;
  language?: string;
};

const ARROW = "-->";
const TIMESTAMP = /^(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[.,](\d+))?$|^(\d+)(?:[.,](\d+))?$/;
const PAUSE = /\bpause\s*=\s*(\d+(?:\.\d+)?)/i;
const ENTITIES: Record<string, string> = {
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
  nbsp: " ",
  lrm: "",
  rlm: "",
};

/** Seconds from `hh:mm:ss.ttt`, `mm:ss.ttt` or plain seconds; NaN when it isn't one. */
export function parseTimestamp(raw: string): number {
  const m = TIMESTAMP.exec(raw.trim());
  if (!m) return Number.NaN;
  if (m[5] !== undefined) return Number(`${m[5]}.${m[6] ?? "0"}`);
  const h = Number(m[1] ?? 0);
  const min = Number(m[2]);
  const s = Number(m[3]);
  if (min > 59 && m[1] !== undefined) return Number.NaN;
  if (s > 59) return Number.NaN;
  const frac = m[4] ? Number(`0.${m[4]}`) : 0;
  return h * 3600 + min * 60 + s + frac;
}

function decodeEntities(s: string): string {
  return s.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (whole, name: string) => {
    if (name[0] === "#") {
      const code = name[1] === "x" || name[1] === "X" ? parseInt(name.slice(2), 16) : parseInt(name.slice(1), 10);
      return Number.isFinite(code) && code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : whole;
    }
    return ENTITIES[name.toLowerCase()] ?? whole;
  });
}

/** Cue payload to one line of plain text: no tags, entities decoded, whitespace collapsed. */
export function cueText(payload: string): string {
  return decodeEntities(payload.replace(/<[^>]*>/g, ""))
    .replace(/\s+/g, " ")
    .trim();
}

function parseTiming(line: string): { start: number; end: number } | null {
  const at = line.indexOf(ARROW);
  if (at < 0) return null;
  const start = parseTimestamp(line.slice(0, at));
  // cue settings follow the end time after whitespace
  const end = parseTimestamp(line.slice(at + ARROW.length).trim().split(/\s+/)[0] ?? "");
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null;
  return { start, end: Math.max(start, end) };
}

const startsWord = (line: string, word: string) => line === word || line.startsWith(`${word} `) || line.startsWith(`${word}\t`);

export function parseVtt(input: string): VttTrack {
  const lines = String(input ?? "")
    .replace(/^﻿/, "")
    .replace(/\r\n?/g, "\n")
    .split("\n");
  const track: VttTrack = { cues: [] };
  let i = 0;

  if (/^WEBVTT(?:[ \t]|$)/.test(lines[0] ?? "")) {
    i = 1;
    for (; i < lines.length; i++) {
      const line = lines[i] ?? "";
      if (!line.trim() || line.includes(ARROW)) break;
      const kv = /^([A-Za-z-]+)\s*:\s*(.+)$/.exec(line.trim());
      if (!kv) continue;
      const key = (kv[1] ?? "").toLowerCase();
      const value = (kv[2] ?? "").trim();
      if (key === "kind") track.kind = value;
      else if (key === "language") track.language = value;
    }
  }

  let pause: number | undefined;
  while (i < lines.length) {
    // gather one block: the lines up to the next blank line
    while (i < lines.length && !(lines[i] ?? "").trim()) i++;
    const block: string[] = [];
    while (i < lines.length && (lines[i] ?? "").trim()) block.push((lines[i++] ?? "").trim());
    if (!block.length) break;

    const first = block[0] ?? "";
    if (startsWord(first, "NOTE")) {
      const m = PAUSE.exec(block.join(" "));
      if (m) pause = Number(m[1]);
      continue;
    }
    if (startsWord(first, "STYLE") || startsWord(first, "REGION")) continue;

    // a block may hold more than one cue when the blank line between them is missing:
    // the line just before a later timing line is that cue's id when the cue before it
    // already has a line of text, otherwise it is that text
    const timings = block.flatMap((line, at) => (line.includes(ARROW) ? [at] : []));
    if (!timings.length) continue;
    const ownsIdLine = (k: number) => {
      const at = timings[k] ?? 0;
      const prev = timings[k - 1];
      return prev !== undefined && at - 1 > prev + 1;
    };
    timings.forEach((at, k) => {
      const next = timings[k + 1];
      const stop = next === undefined ? block.length : ownsIdLine(k + 1) ? next - 1 : next;
      const idLines = k === 0 ? block.slice(0, at) : ownsIdLine(k) ? [block[at - 1] ?? ""] : [];
      const timing = parseTiming(block[at] ?? "");
      if (!timing) {
        pause = undefined;
        return;
      }
      const text = cueText(block.slice(at + 1, stop).join("\n"));
      if (!text) {
        pause = undefined;
        return;
      }
      const cue: VttCue = { id: idLines.join(" ").trim(), start: timing.start, end: timing.end, text };
      if (pause !== undefined && pause > 0) cue.pause = pause;
      pause = undefined;
      track.cues.push(cue);
    });
  }

  track.cues.sort((a, b) => a.start - b.start);
  return track;
}
