/**
 * Speaks a description cue: its recorded clip through Web Audio when one exists, otherwise
 * the system voice (speechSynthesis). Both follow the video's playback rate, so a line keeps
 * to its pause at any speed. Clips are fetched and decoded ahead of the playhead and kept in
 * a small cache.
 *
 * Recordings are `<base>/<cue id>.<ext>` (the engine's export writes .m4a); when that file
 * is missing the same name is tried once as .wav. A base containing `{id}` is used as is.
 */
export type VoiceSource = "clip" | "system" | "none";
/**
 * recorded: lines have recordings. system: there are none to look for (no base).
 * fallback: recordings were looked for and none could be loaded; the system voice speaks.
 * mixed: some lines have recordings, the system voice speaks the rest. none: nothing can speak.
 */
export type VoiceStatus = "recorded" | "system" | "fallback" | "mixed" | "none";

export type Playback = {
  readonly source: VoiceSource;
  /** Resolves when the line has finished or was stopped. */
  readonly done: Promise<void>;
  stop(): void;
  /** Follows a new playback rate mid-line. */
  setRate(rate: number): void;
};

export type SpeakableCue = { id: string; text: string };

export const DEFAULT_EXT = "m4a";
const FALLBACK_EXT = "wav";
const SAFE_ID = /^[A-Za-z0-9_-]+$/;
const SAFE_EXT = /^[a-z0-9]{1,8}$/;
const CACHE_MAX = 32;
/** How long a line may wait for its clip to arrive before the system voice takes it. */
const CLIP_WAIT_MS = 600;
/** Speaking rate assumed when guarding against a system voice that never reports its end. */
const GUARD_WPS = 1.8;

/**
 * The URLs to try for a cue's recording, in order: `<base>/<id>.<ext>`, then the same name as
 * .wav. A base containing `{id}` gives exactly that URL. Empty when there is nothing to fetch.
 */
export function clipUrls(base: string | null | undefined, id: string, ext: string = DEFAULT_EXT): string[] {
  if (!base || !SAFE_ID.test(id)) return [];
  if (base.includes("{id}")) return [base.split("{id}").join(encodeURIComponent(id))];
  const wanted = String(ext ?? "").replace(/^\./, "").toLowerCase();
  const e = SAFE_EXT.test(wanted) ? wanted : DEFAULT_EXT;
  const stem = `${base}${base.endsWith("/") ? "" : "/"}${encodeURIComponent(id)}.`;
  return e === FALLBACK_EXT ? [stem + FALLBACK_EXT] : [stem + e, stem + FALLBACK_EXT];
}

const NONE: Playback = { source: "none", done: Promise.resolve(), stop: () => undefined, setRate: () => undefined };
const hasSpeech = () => typeof speechSynthesis !== "undefined" && typeof SpeechSynthesisUtterance !== "undefined";

function after<T>(ms: number, value: T): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

const isAbort = (err: unknown) => (err as { name?: string } | null)?.name === "AbortError";

export class Voice {
  base: string | null = null;
  ext = DEFAULT_EXT;
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
  private rate = 1;
  /** After a .wav stood in for a missing file, try .wav first. */
  private wavFirst = false;
  private aborter: AbortController | null = typeof AbortController !== "undefined" ? new AbortController() : null;

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
    if (failed && !ok) return speech ? "fallback" : "none";
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

  /** The video's playback rate: lines in flight and lines to come follow it. */
  setRate(rate: number): void {
    this.rate = Number.isFinite(rate) && rate > 0 ? rate : 1;
    for (const p of this.live) p.setRate(this.rate);
  }

  /** Forgets every clip (the base, the extension or the credentials changed). */
  reset(): void {
    this.abort();
    this.cache.clear();
    this.results.clear();
    this.wavFirst = false;
  }

  /** Cancels clip downloads in flight; they count as neither found nor missing. */
  abort(): void {
    this.aborter?.abort();
    this.aborter = typeof AbortController !== "undefined" ? new AbortController() : null;
  }

  /** The decoded clip for a cue, or null when there is none. Cached. */
  load(id: string): Promise<AudioBuffer | null> {
    let urls = clipUrls(this.base, id, this.ext);
    if (!urls.length) return Promise.resolve(null);
    if (this.wavFirst && urls.length > 1) urls = [...urls].reverse();
    const key = `${this.base}|${this.ext}|${id}`;
    const hit = this.cache.get(key);
    if (hit) {
      // refresh its place in the cache
      this.cache.delete(key);
      this.cache.set(key, hit);
      return hit;
    }
    const signal = this.aborter?.signal;
    const p: Promise<AudioBuffer | null> = this.fetchFirst(urls, signal).then((found) => {
      if (found === "aborted") {
        if (this.cache.get(key) === p) this.cache.delete(key);
        return null;
      }
      const buf = found?.buf ?? null;
      if (found && found.url !== urls[0] && found.url.endsWith(`.${FALLBACK_EXT}`)) this.wavFirst = true;
      const had = this.results.get(id);
      this.results.set(id, !!buf);
      if (had !== !!buf) this.onStatus?.();
      // a miss is remembered too, so the line it belongs to goes straight to the system voice
      return buf;
    });
    this.cache.set(key, p);
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
    if (clipUrls(this.base, cue.id, this.ext).length) {
      const buf = await Promise.race([this.load(cue.id), after(CLIP_WAIT_MS, null)]);
      if (!current()) return NONE;
      const ctx = this.getCtx();
      if (buf && ctx && ctx.state === "running") {
        const waited = ((Date.now() - t0) / 1000) * this.rate;
        return this.track(this.playClip(ctx, buf, offset + (waited < 1 ? waited : 0)));
      }
    }
    if (!current()) return NONE;
    if (hasSpeech()) return this.track(this.speak(cue.text));
    return NONE;
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

  /** The first URL that gives a decodable clip; null when none does; "aborted" when cancelled. */
  private async fetchFirst(urls: string[], signal: AbortSignal | undefined): Promise<{ url: string; buf: AudioBuffer } | null | "aborted"> {
    for (const url of urls) {
      try {
        const res = await fetch(url, { credentials: this.credentials, signal });
        if (!res.ok) {
          // let the connection go: an unread error body keeps the request open
          void res.body?.cancel().catch(() => undefined);
          continue;
        }
        const data = await res.arrayBuffer();
        return { url, buf: await this.decode(data) };
      } catch (err) {
        if (isAbort(err) || signal?.aborted) return "aborted";
      }
    }
    return null;
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
    src.playbackRate.value = this.rate;
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
      setRate: (rate) => src.playbackRate.setValueAtTime(rate, ctx.currentTime),
    };
  }

  private speak(text: string): Playback {
    const u = new SpeechSynthesisUtterance(text);
    u.rate = this.rate;
    u.volume = this.level;
    if (this.lang) u.lang = this.lang;
    let finish: () => void = () => undefined;
    const done = new Promise<void>((resolve) => {
      finish = resolve;
    });
    // some engines never report the end of a line (or have no voices): give up after a fair time
    const words = text.split(/\s+/).filter(Boolean).length;
    const guard = setTimeout(() => finish(), (words / (GUARD_WPS * this.rate) + 1.5) * 1000);
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
      // an utterance keeps the rate it started with; the next line takes the new one
      setRate: () => undefined,
    };
  }
}
