/**
 * The programme's loudness: the viewer's volume, and a −10 dB duck with 200 ms ramps while
 * a description speaks.
 *
 * Two ways to apply it, same interface:
 * - GraphProgramme routes the video through Web Audio (MediaElementAudioSourceNode into a
 *   GainNode) and ramps the gain on the audio clock. Used when the media is same-origin or
 *   CORS-enabled (otherwise the browser would hand Web Audio silence) and the audio
 *   context is running.
 * - ElementProgramme ramps `media.volume` on a timer. The fallback for cross-origin media
 *   without CORS, and the state before the viewer's first press starts the audio context.
 */
export const DUCK_DB = -10;
export const RAMP_S = 0.2;
/** A volume change by the viewer: quick, but without a click. */
const LEVEL_RAMP_S = 0.03;

export const dbToGain = (db: number): number => 10 ** (db / 20);
const DUCKED = dbToGain(DUCK_DB);
const clamp01 = (v: number) => (Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : 0);

export interface Programme {
  readonly kind: "graph" | "element";
  readonly ducked: boolean;
  /** The programme's level before ducking, 0 to 1 (0 when muted or silenced by the mode). */
  setLevel(level: number): void;
  duck(on: boolean): void;
  /** Stops pending ramps; the last level stays applied. */
  dispose(): void;
}

type Clock = { now(): number; every(fn: () => void, ms: number): () => void };

const realClock: Clock = {
  now: () => (typeof performance !== "undefined" ? performance.now() : Date.now()),
  every: (fn, ms) => {
    const id = setInterval(fn, ms);
    return () => clearInterval(id);
  },
};

/** Ramps the element's own volume. Linear in gain over RAMP_S, stepped every 16 ms. */
export class ElementProgramme implements Programme {
  readonly kind = "element" as const;
  private level: number;
  private factor = 1;
  private target = 1;
  private stop: (() => void) | null = null;
  private readonly media: Pick<HTMLMediaElement, "volume">;
  private readonly clock: Clock;

  constructor(media: Pick<HTMLMediaElement, "volume">, { level = 1, ducked = false, clock = realClock }: { level?: number; ducked?: boolean; clock?: Clock } = {}) {
    this.media = media;
    this.clock = clock;
    this.level = clamp01(level);
    this.factor = this.target = ducked ? DUCKED : 1;
    this.apply();
  }

  get ducked(): boolean {
    return this.target < 1;
  }

  setLevel(level: number): void {
    this.level = clamp01(level);
    this.apply();
  }

  duck(on: boolean): void {
    const target = on ? DUCKED : 1;
    if (target === this.target) return;
    this.target = target;
    this.stop?.();
    const from = this.factor;
    const t0 = this.clock.now();
    const step = () => {
      const k = Math.min(1, (this.clock.now() - t0) / (RAMP_S * 1000));
      this.factor = from + (target - from) * k;
      this.apply();
      if (k >= 1) {
        this.stop?.();
        this.stop = null;
      }
    };
    this.stop = this.clock.every(step, 16);
    step();
  }

  dispose(): void {
    this.stop?.();
    this.stop = null;
  }

  private apply(): void {
    try {
      this.media.volume = clamp01(this.level * this.factor);
    } catch {
      // iOS keeps media volume read-only; the graph is the way to duck there
    }
  }
}

type GainLike = {
  value: number;
  cancelScheduledValues(t: number): unknown;
  setValueAtTime(v: number, t: number): unknown;
  linearRampToValueAtTime(v: number, t: number): unknown;
};

/** Ramps a GainNode the video is routed through. The routing is permanent for the element. */
export class GraphProgramme implements Programme {
  readonly kind = "graph" as const;
  private level: number;
  private factor: number;
  private readonly ctx: { currentTime: number };
  private readonly gain: GainLike;

  constructor(
    media: HTMLMediaElement,
    ctx: AudioContext,
    { level = 1, ducked = false, node }: { level?: number; ducked?: boolean; node?: { gain: GainLike } } = {},
  ) {
    this.ctx = ctx;
    this.level = clamp01(level);
    this.factor = ducked ? DUCKED : 1;
    if (node) {
      this.gain = node.gain;
    } else {
      const source = ctx.createMediaElementSource(media);
      const g = ctx.createGain();
      source.connect(g).connect(ctx.destination);
      this.gain = g.gain;
      // the gain carries the viewer's volume from now on
      media.volume = 1;
    }
    this.gain.value = this.level * this.factor;
  }

  get ducked(): boolean {
    return this.factor < 1;
  }

  setLevel(level: number): void {
    this.level = clamp01(level);
    this.ramp(LEVEL_RAMP_S);
  }

  duck(on: boolean): void {
    const factor = on ? DUCKED : 1;
    if (factor === this.factor) return;
    this.factor = factor;
    this.ramp(RAMP_S);
  }

  dispose(): void {
    this.gain.cancelScheduledValues(this.ctx.currentTime);
    this.gain.value = this.level * this.factor;
  }

  private ramp(seconds: number): void {
    const t = this.ctx.currentTime;
    const p = this.gain;
    p.cancelScheduledValues(t);
    p.setValueAtTime(p.value, t);
    p.linearRampToValueAtTime(this.level * this.factor, t + seconds);
  }
}
