/**
 * Speaks a description cue: its recorded clip through Web Audio when one exists, otherwise
 * the system voice (speechSynthesis at rate 1.0). Clips are fetched and decoded ahead of
 * the playhead and kept in a small cache.
 */
export type VoiceSource = "clip" | "system" | "none";
export type VoiceStatus = "recorded" | "system" | "mixed" | "none";

export type Playback = {
  readonly source: VoiceSource;
  /** Resolves when the line has finished or was stopped. */
  readonly done: Promise<void>;
  stop(): void;
};

export type SpeakableCue = { id: string; text: string };

const SAFE_ID = /^[A-Za-z0-9_-]+$/;
const CACHE_MAX = 32;
/** How long a line may wait for its clip to arrive before the system voice takes it. */
const CLIP_WAIT_MS = 600;
/** Speaking rate assumed when guarding against a system voice that never reports its end. */
const GUARD_WPS = 1.8;

/** The URL of a cue's recording: `{id}` in the base is replaced, otherwise `<base>/<id>.wav`. */
export function clipUrl(base: string | null | undefined, id: string): string | null {
  if (!base || !SAFE_ID.test(id)) return null;
  if (base.includes("{id}")) return base.split("{id}").join(encodeURIComponent(id));
  return `${base}${base.endsWith("/") ? "" : "/"}${encodeURIComponent(id)}.wav`;
}

const NONE: Playback = { source: "none", done: Promise.resolve(), stop: () => undefined };
const hasSpeech = () => typeof speechSynthesis !== "undefined" && typeof SpeechSynthesisUtterance !== "undefined";

function after<T>(ms: number, value: T): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

export class Voice {
  base: string | null = null;
  lang = "";
  credentials: RequestCredentials = "same-origin";
  /** Called when what the voice can do changes (a clip arrived or failed). */
  onStatus?: () => void;

  private readonly getCtx: () => AudioContext | null;
  private readonly cache = new Map<string, Promise<AudioBuffer | null>>();
  private readonly results = new Map<string, boolean>();
  private readonly live = new Set<Playback>();
  private bus: GainNode | null = null;
  private busCtx: AudioContext | null = null;
  private level = 1;

  constructor(getCtx: () => AudioContext | null) {
    this.getCtx = getCtx;
  }

  get systemVoice(): boolean {
    return hasSpeech();
  }

  /** What a line will be spoken with, as far as is known. */
  status(): VoiceStatus {
    const speech = hasSpeech();
    if (!this.base) return speech ? "system" : "none";
    const results = [...this.results.values()];
    const ok = results.filter(Boolean).length;
    const failed = results.length - ok;
    if (failed && !ok) return speech ? "system" : "none";
    if (failed && ok) return speech ? "mixed" : "recorded";
    return "recorded";
  }

  /** True when lines can be spoken at all. */
  canSpeak(): boolean {
    return this.status() !== "none";
  }

  setVolume(level: number): void {
    this.level = Math.min(1, Math.max(0, level));
    if (this.bus && this.busCtx) {
      const t = this.busCtx.currentTime;
      this.bus.gain.cancelScheduledValues(t);
      this.bus.gain.setValueAtTime(this.bus.gain.value, t);
      this.bus.gain.linearRampToValueAtTime(this.level, t + 0.03);
    }
  }

  /** The decoded clip for a cue, or null when there is none. Cached. */
  load(id: string): Promise<AudioBuffer | null> {
    const url = clipUrl(this.base, id);
    if (!url) return Promise.resolve(null);
    const hit = this.cache.get(url);
    if (hit) {
      // refresh its place in the cache
      this.cache.delete(url);
      this.cache.set(url, hit);
      return hit;
    }
    const p = this.fetchClip(url).then((buf) => {
      const had = this.results.get(id);
      this.results.set(id, !!buf);
      if (had !== !!buf) this.onStatus?.();
      if (!buf) this.cache.delete(url);
      return buf;
    });
    this.cache.set(url, p);
    while (this.cache.size > CACHE_MAX) {
      const oldest = this.cache.keys().next().value;
      if (oldest === undefined) break;
      this.cache.delete(oldest);
    }
    return p;
  }

  prefetch(ids: readonly string[]): void {
    for (const id of ids) void this.load(id);
  }

  /**
   * Speaks a line. `offset` skips into the clip (a line that fired late still ends on time).
   * `current()` is asked after any wait; when it returns false nothing is spoken.
   */
  async play(cue: SpeakableCue, { offset = 0, current = () => true }: { offset?: number; current?: () => boolean } = {}): Promise<Playback> {
    const t0 = Date.now();
    if (clipUrl(this.base, cue.id)) {
      const buf = await Promise.race([this.load(cue.id), after(CLIP_WAIT_MS, null)]);
      if (!current()) return NONE;
      const ctx = this.getCtx();
      if (buf && ctx && ctx.state === "running") {
        const waited = (Date.now() - t0) / 1000;
        return this.track(this.playClip(ctx, buf, offset + (waited < 1 ? waited : 0)));
      }
    }
    if (!current()) return NONE;
    if (hasSpeech()) return this.track(this.speak(cue.text));
    return NONE;
  }

  /** Forgets every clip (the base changed). */
  reset(): void {
    this.cache.clear();
    this.results.clear();
  }

  /** Stops lines spoken by the system voice; clips are left to the audio clock. */
  stopSpeech(): void {
    for (const p of [...this.live]) if (p.source === "system") p.stop();
  }

  /** Stops every line that is speaking. */
  stopAll(): void {
    for (const p of [...this.live]) p.stop();
    this.live.clear();
    if (hasSpeech()) speechSynthesis.cancel();
  }

  get speaking(): boolean {
    return this.live.size > 0;
  }

  private track(p: Playback): Playback {
    if (p.source === "none") return p;
    this.live.add(p);
    void p.done.then(() => this.live.delete(p));
    return p;
  }

  private async fetchClip(url: string): Promise<AudioBuffer | null> {
    try {
      const res = await fetch(url, { credentials: this.credentials });
      if (!res.ok) {
        void res.body?.cancel().catch(() => undefined);
        return null;
      }
      const data = await res.arrayBuffer();
      return await this.decode(data);
    } catch {
      return null;
    }
  }

  private decode(data: ArrayBuffer): Promise<AudioBuffer> {
    // an offline context decodes without waiting for the viewer's first press
    const Offline = typeof OfflineAudioContext !== "undefined" ? OfflineAudioContext : null;
    const ctx: BaseAudioContext | null = Offline ? new Offline(1, 1, 44100) : this.getCtx();
    if (!ctx) return Promise.reject(new Error("no audio context"));
    return new Promise((resolve, reject) => {
      const p = ctx.decodeAudioData(data, resolve, reject);
      if (p && typeof p.then === "function") p.then(resolve, reject);
    });
  }

  private busFor(ctx: AudioContext): GainNode {
    if (!this.bus || this.busCtx !== ctx) {
      this.bus = ctx.createGain();
      this.bus.gain.value = this.level;
      this.bus.connect(ctx.destination);
      this.busCtx = ctx;
    }
    return this.bus;
  }

  private playClip(ctx: AudioContext, buf: AudioBuffer, offset: number): Playback {
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(this.busFor(ctx));
    let finish: () => void = () => undefined;
    const done = new Promise<void>((resolve) => {
      finish = resolve;
    });
    src.onended = () => finish();
    src.start(0, Math.min(Math.max(0, offset), Math.max(0, buf.duration - 0.05)));
    return {
      source: "clip",
      done,
      stop: () => {
        try {
          src.stop();
        } catch {
          // already stopped
        }
        finish();
      },
    };
  }

  private speak(text: string): Playback {
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1;
    u.volume = this.level;
    if (this.lang) u.lang = this.lang;
    let finish: () => void = () => undefined;
    const done = new Promise<void>((resolve) => {
      finish = resolve;
    });
    // some engines never report the end of a line (or have no voices): give up after a fair time
    const words = text.split(/\s+/).filter(Boolean).length;
    const guard = setTimeout(() => finish(), (words / GUARD_WPS + 1.5) * 1000);
    const end = () => {
      clearTimeout(guard);
      finish();
    };
    u.onend = end;
    u.onerror = end;
    speechSynthesis.speak(u);
    return {
      source: "system",
      done,
      stop: () => {
        speechSynthesis.cancel();
        end();
      },
    };
  }
}
