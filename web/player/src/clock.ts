/**
 * Decides when description cues fire as the programme plays.
 *
 * A cue fires once, when the playhead reaches its start. After a seek, cues that start
 * before the new position count as passed and a cue starting exactly there is due, so
 * jumping to a line from the transcript speaks it. A cue the playhead passed long ago
 * (a throttled background tab, a stall) is dropped rather than spoken late over speech.
 */
export type TimedCue = { start: number; end: number };

export type DueOptions = {
  /** Fire this many seconds early, to absorb timer jitter. */
  lead?: number;
  /** Seconds after its start a cue may still fire. */
  late?: number;
  /** Also fire a late cue while the playhead is still inside it (screen reader lines). */
  untilEnd?: boolean;
};

const SEEK_EPS = 0.03;

export class CueClock<C extends TimedCue = TimedCue> {
  readonly cues: readonly C[];
  private fired: boolean[];

  constructor(cues: readonly C[]) {
    this.cues = cues;
    this.fired = cues.map(() => false);
  }

  /** After a seek or a mode change: cues starting before `t` are passed; one starting at `t` is due. */
  reset(t: number): void {
    this.fired = this.cues.map((c) => c.start < t - SEEK_EPS);
  }

  /** Counts cue `i` as fired (it was just read out another way). */
  skip(i: number): void {
    if (i >= 0 && i < this.fired.length) this.fired[i] = true;
  }

  /** Lets cue `i` fire again (a hold the viewer interrupted). */
  rearm(i: number): void {
    if (i >= 0 && i < this.fired.length) this.fired[i] = false;
  }

  /** Indices of the cues to fire at `now`, in order. Each index is returned once. */
  due(now: number, { lead = 0.02, late = 0.75, untilEnd = false }: DueOptions = {}): number[] {
    const out: number[] = [];
    this.cues.forEach((c, i) => {
      if (this.fired[i] || c.start > now + lead) return;
      this.fired[i] = true;
      if (now - c.start <= late || (untilEnd && now < c.end)) out.push(i);
    });
    return out;
  }

  /** Start of the next cue still to fire after `now`, if any. */
  next(now: number): number | undefined {
    let best: number | undefined;
    this.cues.forEach((c, i) => {
      if (!this.fired[i] && c.start > now && (best === undefined || c.start < best)) best = c.start;
    });
    return best;
  }

  /** The cue the playhead is inside (the latest-starting one), or -1. */
  active(now: number): number {
    let at = -1;
    this.cues.forEach((c, i) => {
      if (c.start <= now && now < c.end && (at < 0 || c.start >= (this.cues[at]?.start ?? 0))) at = i;
    });
    return at;
  }
}

/** The longest an extended cue may hold the picture, whatever its file says. */
export const MAX_HOLD_S = 30;
const MIN_HOLD_S = 0.5;

/**
 * Seconds the programme waits for an extended cue: its `NOTE extended pause=`, or failing that
 * its span (at least half a second), never more than MAX_HOLD_S.
 */
export function holdFor(c: { start: number; end: number; pause?: number }): number {
  const said = c.pause !== undefined && Number.isFinite(c.pause) && c.pause > 0 ? c.pause : Math.max(MIN_HOLD_S, c.end - c.start);
  return Math.min(MAX_HOLD_S, said);
}

/**
 * Wall-clock seconds the picture holds for an extended cue at playback `speed`: the hold
 * shortens or lengthens with the speed, as the line's voice does, and is bounded again
 * afterwards, so a slow speed never stretches it past MAX_HOLD_S.
 */
export function holdWall(c: { start: number; end: number; pause?: number }, speed: number): number {
  const rate = Number.isFinite(speed) && speed > 0 ? speed : 1;
  return Math.min(MAX_HOLD_S, holdFor(c) / rate);
}
