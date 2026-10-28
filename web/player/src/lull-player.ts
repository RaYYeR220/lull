/**
 * <lull-player>: a video player that makes Lull's audio descriptions usable.
 *
 * Browsers don't speak `<track kind="descriptions">`, so the player does: four ways to hear
 * (or read) the descriptions, every control reachable by keyboard, and every change of
 * state said through a polite live region.
 *
 *   <script type="module" src="https://…/player.js"></script>
 *   <lull-player src="talk.mp4" descriptions="descriptions.vtt" described-audio="described-audio.m4a"
 *                extended="extended.vtt" cue-audio-base="audio/" captions="captions.vtt"
 *                poster="poster.jpg" label="Lecture 3"></lull-player>
 */
import { CueClock, holdFor, holdWall } from "./clock";
import { ElementProgramme, GraphProgramme, type Programme } from "./duck";
import { MediaFollower } from "./sync";
import { clock, seconds2, spokenTime } from "./time";
import { ICON, STYLE, TEMPLATE } from "./view";
import { DEFAULT_EXT, Voice, type Playback, type VoiceSource, type VoiceStatus } from "./voice";
import { parseVtt, type VttCue } from "./vtt";

export type PlayerMode = "mixed" | "spoken" | "screen-reader" | "extended";
export const MODES: readonly PlayerMode[] = ["mixed", "spoken", "screen-reader", "extended"];

export type CueKind = "standard" | "extended";
export type Cue = VttCue & { kind: CueKind; index: number };

/** Detail of the `lull-cue` event, sent when a description starts. */
export type CueEventDetail = {
  id: string;
  text: string;
  start: number;
  kind: CueKind;
  mode: PlayerMode;
  /** How it reached the viewer. */
  via: VoiceSource | "screen-reader";
  /** Seconds the video waits, for an extended cue. */
  hold?: number;
};

/** Detail of the `lull-modechange` event. */
export type ModeEventDetail = { mode: PlayerMode | null; requested: PlayerMode | null };

/** A read-only snapshot, for pages and tests. */
export type PlayerState = {
  mode: PlayerMode | null;
  playing: boolean;
  holding: boolean;
  ducked: boolean;
  programme: "graph" | "element";
  voice: ReturnType<Voice["status"]>;
  volume: number;
  muted: boolean;
  speed: number;
  captions: boolean;
  video: Load;
  descriptions: Load;
  extended: Load;
  describedAudio: Load;
  activeCue: string | null;
};

type Load = "none" | "loading" | "ready" | "empty" | "error";

const MODE_NAME: Record<PlayerMode, string> = {
  mixed: "Mixed audio",
  spoken: "Spoken cues",
  "screen-reader": "My screen reader",
  extended: "Extended",
};
const MODE_DESC: Record<PlayerMode, string> = {
  mixed: "The described soundtrack, mixed in advance. The video’s own sound is off.",
  spoken: "The original soundtrack. Each line is spoken in its pause and the sound dips under it.",
  "screen-reader": "Each line goes to your screen reader as it comes up. Nothing is spoken here.",
  extended: "Like spoken cues, and the video waits while a longer line is spoken.",
};
const IDLE: Record<PlayerMode, string> = {
  mixed: "Each description shows here as the soundtrack speaks it.",
  spoken: "Each description shows here as it is spoken.",
  "screen-reader": "Each description shows here as it goes to your screen reader.",
  extended: "Each description shows here as it is spoken. The video waits for the longer ones.",
};

const SEEK_STEP = 5;
const VOLUME_STEP = 0.1;
const SPEEDS = [0.75, 1, 1.25] as const;
const STORE_KEY = "lull-player-mode";
/** How late (s) a spoken line may still start; an extended hold snaps back, so it may be later. */
const LATE_SPOKEN = 0.75;
const LATE_HOLD = 2;
/** A hold whose voice never reports its end gives up this long after the pause. */
const HOLD_CAP_MS = 8000;
const PREFETCH_AHEAD = 20;

const ATTRS = ["src", "descriptions", "described-audio", "extended", "cue-audio-base", "cue-audio-ext", "captions", "poster", "mode", "label", "crossorigin"] as const;

const isMode = (v: unknown): v is PlayerMode => typeof v === "string" && (MODES as readonly string[]).includes(v);
const speaks = (m: PlayerMode | null) => m === "spoken" || m === "extended";

type Hold = {
  cue: Cue;
  remaining: number;
  since: number;
  timer: ReturnType<typeof setTimeout> | undefined;
  cap: ReturnType<typeof setTimeout> | undefined;
  playback: Playback | null;
  timeUp: boolean;
  voiceDone: boolean;
  respeak: boolean;
};

type Els = {
  player: HTMLElement;
  stage: HTMLElement;
  video: HTMLVideoElement;
  audio: HTMLAudioElement;
  notice: HTMLElement;
  nTitle: HTMLElement;
  nBody: HTMLElement;
  nUrl: HTMLElement;
  band: HTMLElement;
  hold: HTMLElement;
  holdWait: HTMLElement;
  line: HTMLElement;
  idle: HTMLElement;
  track: HTMLElement;
  spans: HTMLElement;
  seek: HTMLInputElement;
  play: HTMLButtonElement;
  now: HTMLElement;
  dur: HTMLElement;
  mode: HTMLButtonElement;
  modeName: HTMLElement;
  menu: HTMLElement;
  group: HTMLElement;
  speed: HTMLButtonElement;
  speedValue: HTMLElement;
  cc: HTMLButtonElement;
  tx: HTMLButtonElement;
  keys: HTMLButtonElement;
  mute: HTMLButtonElement;
  volume: HTMLInputElement;
  fs: HTMLButtonElement;
  meta: HTMLElement;
  transcript: HTMLElement;
  lines: HTMLElement;
  count: HTMLElement;
  empty: HTMLElement;
  keymap: HTMLElement;
  liveStatus: HTMLElement;
  liveCue: HTMLElement;
};

const Base: typeof HTMLElement = typeof HTMLElement !== "undefined" ? HTMLElement : (class {} as unknown as typeof HTMLElement);

export class LullPlayer extends Base {
  static get observedAttributes(): readonly string[] {
    return ATTRS;
  }

  private readonly els: Els;
  private readonly voice: Voice;
  private programme: Programme;
  private follower: MediaFollower | null = null;
  private ctx: AudioContext | null = null;

  private requested: PlayerMode | null = null;
  private current: PlayerMode | null = null;
  private desc: { state: Load; cues: Cue[] } = { state: "none", cues: [] };
  private ext: { state: Load; cues: Cue[] } = { state: "none", cues: [] };
  private all: Cue[] = [];
  private descClock = new CueClock<Cue>([]);
  private extClock = new CueClock<Cue>([]);
  private videoState: Load = "none";
  private mixedState: Load = "none";

  private want = false;
  private hold: Hold | null = null;
  private bufferHold = false;
  private ducks = 0;
  private gen = 0;
  private volume = 1;
  private muted = false;
  private speed = 1;
  private captionsOn = false;
  private interacted = false;
  private raf = 0;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private timerKey = "";
  private lastPrefetch = -Infinity;
  private lastCue: Cue | null = null;
  private activeKey = "";
  private timeKey = "";
  private loads: Record<"desc" | "ext", AbortController | null> = { desc: null, ext: null };
  private rowEls = new Map<Cue, HTMLButtonElement>();
  private spanEls = new Map<Cue, HTMLElement>();
  private announceTimer: Record<"status" | "cue", ReturnType<typeof setTimeout> | undefined> = { status: undefined, cue: undefined };
  private connectedOnce = false;
  /** Track loads cut off by leaving the page, to run again on return. */
  private reload = new Set<"desc" | "ext">();
  /** Lines in flight, by playback. */
  private speaking = new Map<Playback, Cue>();
  /** System-voice lines a pause cut short, said again on play. */
  private interrupted: Cue[] = [];
  /** What the voice was last known to be; a change to fallback or mixed is said once. */
  private voiceSeen: VoiceStatus = "none";
  private voiceNotice = false;
  /** The video is reloading under a new crossorigin setting: its pause events are ours. */
  private reloading = false;
  private seekAnnounced = "";

  constructor() {
    super();
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `<style>${STYLE}</style>${TEMPLATE}`;
    const q = <T extends Element = HTMLElement>(sel: string) => root.querySelector(sel) as T;
    this.els = {
      player: q(".player"),
      stage: q(".stage"),
      video: q<HTMLVideoElement>("video"),
      audio: q<HTMLAudioElement>("audio"),
      notice: q(".notice"),
      nTitle: q(".n-title"),
      nBody: q(".n-body"),
      nUrl: q(".n-url"),
      band: q(".band"),
      hold: q(".hold"),
      holdWait: q(".hold .w"),
      line: q(".line"),
      idle: q(".idle"),
      track: q(".track"),
      spans: q(".spans"),
      seek: q<HTMLInputElement>(".seek"),
      play: q<HTMLButtonElement>(".play"),
      now: q(".now"),
      dur: q(".dur"),
      mode: q<HTMLButtonElement>(".mode"),
      modeName: q(".mode .v"),
      menu: q(".menu"),
      group: q(".group"),
      speed: q<HTMLButtonElement>(".speed"),
      speedValue: q(".speed .v"),
      cc: q<HTMLButtonElement>(".cc"),
      tx: q<HTMLButtonElement>(".tx"),
      keys: q<HTMLButtonElement>(".keys"),
      mute: q<HTMLButtonElement>(".mute"),
      volume: q<HTMLInputElement>(".volume"),
      fs: q<HTMLButtonElement>(".fs"),
      meta: q(".meta"),
      transcript: q("#transcript"),
      lines: q(".lines"),
      count: q(".count"),
      empty: q(".empty"),
      keymap: q("#keys"),
      liveStatus: q('[data-live="status"]'),
      liveCue: q('[data-live="cue"]'),
    };
    this.voice = new Voice(() => this.ctx);
    this.voiceSeen = this.voice.status();
    this.voice.onStatus = () => {
      this.noteVoice();
      this.resolveMode();
      this.renderAll();
    };
    this.programme = new ElementProgramme(this.els.video);
    this.buildMenu();
    this.wire();
    this.wireVideo(this.els.video);
    this.renderAll();
  }

  // ---------------------------------------------------------------- public API

  /** The mode in use (null when the video has no descriptions at all). Setting it asks for a mode. */
  get mode(): PlayerMode | null {
    return this.current;
  }
  set mode(m: PlayerMode | null) {
    if (isMode(m)) this.choose(m, false);
  }

  get currentTime(): number {
    return this.els.video.currentTime;
  }

  get state(): PlayerState {
    return {
      mode: this.current,
      playing: this.want,
      holding: !!this.hold,
      ducked: this.programme.ducked,
      programme: this.programme.kind,
      voice: this.voice.status(),
      volume: this.volume,
      muted: this.muted,
      speed: this.speed,
      captions: this.captionsOn,
      video: this.videoState,
      descriptions: this.desc.state,
      extended: this.ext.state,
      describedAudio: this.mixedState,
      activeCue: this.lastCue && this.isActive(this.lastCue) ? this.lastCue.id : null,
    };
  }

  play(): void {
    this.userPlay();
  }

  pause(): void {
    this.userPause();
  }

  /** Jumps to `seconds` (clamped to the video). */
  seek(seconds: number): void {
    this.seekTo(seconds);
  }

  // ---------------------------------------------------------------- lifecycle

  connectedCallback(): void {
    if (!this.hasAttribute("mode")) {
      const stored = this.readStoredMode();
      if (stored) this.requested = stored;
    }
    document.addEventListener("fullscreenchange", this.onFullscreen);
    document.addEventListener("webkitfullscreenchange", this.onFullscreen);
    // back in the page: run the loads that leaving cut off, and set the mode up again
    // (the mixed track's follower, the levels), since leaving took it down
    for (const which of this.reload) void this.loadTrack(which, this.getAttribute(which === "desc" ? "descriptions" : "extended"));
    this.reload.clear();
    this.connectedOnce = true;
    this.applyMode(this.pickMode());
    this.renderAll();
  }

  disconnectedCallback(): void {
    document.removeEventListener("fullscreenchange", this.onFullscreen);
    document.removeEventListener("webkitfullscreenchange", this.onFullscreen);
    document.removeEventListener("pointerdown", this.onOutside, true);
    this.want = false;
    this.cancelHold();
    this.silence();
    this.follower?.detach();
    this.follower = null;
    this.els.video.pause();
    this.stopLoop();
    clearTimeout(this.announceTimer.status);
    clearTimeout(this.announceTimer.cue);
    for (const which of ["desc", "ext"] as const) {
      const ctl = this.loads[which];
      if (!ctl) continue;
      ctl.abort();
      this.loads[which] = null;
      this.reload.add(which);
    }
    this.voice.abort();
    // a video routed into Web Audio stays bound to that context, and the context is about to
    // close: give the player a fresh video element so it has sound when it comes back
    if (this.programme.kind === "graph") this.replaceVideo();
    if (this.ctx) {
      void this.ctx.close().catch(() => undefined);
      this.ctx = null;
    }
  }

  private readonly onFullscreen = () => this.renderAll();

  /** A press anywhere but the menu and its button closes the menu. */
  private readonly onOutside = (e: Event) => {
    const path = e.composedPath();
    if (!path.includes(this.els.menu) && !path.includes(this.els.mode)) this.toggleMenu(false, false);
  };

  attributeChangedCallback(name: string, _old: string | null, value: string | null): void {
    const { video, audio } = this.els;
    switch (name) {
      case "src":
        this.want = false;
        this.cancelHold();
        this.silence();
        this.videoState = value ? "loading" : "none";
        if (value) video.src = value;
        else video.removeAttribute("src");
        video.load();
        break;
      case "poster":
        if (value) video.poster = value;
        else video.removeAttribute("poster");
        break;
      case "descriptions":
        void this.loadTrack("desc", value);
        break;
      case "extended":
        void this.loadTrack("ext", value);
        break;
      case "described-audio":
        this.mixedState = value ? "loading" : "none";
        if (value) {
          audio.preload = "metadata";
          audio.src = value;
        } else {
          audio.removeAttribute("src");
        }
        audio.load();
        this.resolveMode();
        break;
      case "cue-audio-base":
        this.voice.base = value || null;
        this.voice.reset();
        this.prefetch(true);
        break;
      case "cue-audio-ext":
        this.voice.ext = value?.trim() || DEFAULT_EXT;
        this.voice.reset();
        this.prefetch(true);
        break;
      case "captions":
        this.setCaptionsTrack(value);
        break;
      case "mode":
        if (isMode(value)) this.requested = value;
        this.resolveMode();
        break;
      case "label":
        this.els.player.setAttribute("aria-label", value?.trim() || "Described video");
        break;
      case "crossorigin": {
        if (_old === value) break;
        const v = value === null ? null : value === "use-credentials" ? "use-credentials" : "anonymous";
        video.crossOrigin = v;
        audio.crossOrigin = v;
        this.voice.credentials = v === "use-credentials" ? "include" : "same-origin";
        // the setting only applies to the next load: load everything again under it
        if (this.connectedOnce) this.reloadMedia();
        break;
      }
    }
    this.renderAll();
  }

  // ---------------------------------------------------------------- wiring

  private wire(): void {
    const { audio, stage, play, seek, mode, menu, group, speed, cc, tx, keys, mute, volume, fs, lines } = this.els;

    play.addEventListener("click", () => this.togglePlay());
    stage.addEventListener("click", () => {
      if (!this.playable()) return;
      stage.focus({ preventScroll: true });
      this.togglePlay();
    });
    seek.addEventListener("input", () => this.seekTo(Number(seek.value), false));
    mode.addEventListener("click", () => this.toggleMenu(!this.menuOpen(), true));
    group.addEventListener("change", (e) => {
      const input = e.target as HTMLInputElement;
      if (isMode(input.value)) this.choose(input.value, true);
    });
    group.addEventListener("click", (e) => {
      // a pointer pick closes the menu; arrow keys move through the options and keep it open
      const input = (e.target as Element).closest?.("input");
      if (input && (e as MouseEvent).detail > 0) this.toggleMenu(false, true);
    });
    menu.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        this.toggleMenu(false, true);
      }
    });
    this.shadowRoot?.addEventListener("focusout", (e) => {
      const next = (e as FocusEvent).relatedTarget as Node | null;
      if (this.menuOpen() && next && !menu.contains(next) && next !== mode) this.toggleMenu(false, false);
    });
    speed.addEventListener("click", () => this.cycleSpeed(1));
    cc.addEventListener("click", () => this.toggleCaptions());
    tx.addEventListener("click", () => this.togglePanel("transcript"));
    keys.addEventListener("click", () => this.togglePanel("keys"));
    mute.addEventListener("click", () => this.setVolume(this.volume, !this.muted, true));
    volume.addEventListener("input", () => this.setVolume(Number(volume.value), Number(volume.value) === 0, false));
    fs.addEventListener("click", () => this.toggleFullscreen());
    lines.addEventListener("click", (e) => {
      const row = (e.target as Element).closest?.("button[data-at]");
      if (row) this.seekTo(Number(row.getAttribute("data-at")), true);
    });
    this.els.player.addEventListener("keydown", (e) => this.onKey(e));

    audio.addEventListener("loadedmetadata", () => {
      this.mixedState = "ready";
      this.resolveMode();
      this.renderAll();
    });
    audio.addEventListener("error", () => {
      if (!this.getAttribute("described-audio")) return;
      this.mixedState = "error";
      this.resolveMode();
      this.renderAll();
    });
  }


  /** The video's events. Events from a video the player has since replaced are ignored. */
  private wireVideo(v: HTMLVideoElement): void {
    const on = (type: string, fn: () => void) =>
      v.addEventListener(type, () => {
        if (v === this.els.video) fn();
      });
    on("loadedmetadata", () => {
      this.videoState = "ready";
      this.reloading = false;
      if (v.videoWidth && v.videoHeight) this.els.stage.style.setProperty("--lp-ar", `${v.videoWidth} / ${v.videoHeight}`);
      this.buildSpans();
      this.renderAll();
    });
    on("error", () => {
      if (!this.getAttribute("src")) return;
      this.reloading = false;
      this.videoState = "error";
      this.want = false;
      this.cancelHold();
      this.silence();
      this.renderAll();
      if (this.interacted) this.say("This video isn’t available.");
    });
    on("play", () => {
      if (this.hold) {
        // only the hold moves the picture on: undo a play from outside (a media key, picture-in-picture)
        v.pause();
        if (Math.abs(v.currentTime - this.hold.cue.start) > 0.02) v.currentTime = this.hold.cue.start;
        return;
      }
      if (!this.want) this.want = true; // started from outside: media keys, picture-in-picture
      void this.ctx?.resume().catch(() => undefined);
      this.loop();
      this.renderAll();
    });
    on("pause", () => {
      // paused from outside (media keys) while we meant to play
      if (this.want && !this.hold && !this.bufferHold && !this.reloading && !v.ended) this.userPause(false);
      this.renderAll();
    });
    on("ended", () => {
      this.want = false;
      this.cancelHold();
      this.stopLoop();
      this.renderAll();
      this.say("Ended.");
    });
    on("seeking", () => {
      if (this.hold && Math.abs(v.currentTime - this.hold.cue.start) < 0.02) return;
      this.onSeek(v.currentTime);
    });
    on("seeked", () => this.renderTime());
    on("timeupdate", () => this.tick());
    on("durationchange", () => {
      this.buildSpans();
      this.renderTime();
    });
    on("ratechange", () => {
      if (v.playbackRate !== this.speed) this.speed = v.playbackRate;
      this.voice.setRate(this.speed);
      this.renderAll();
    });
  }

  /** A fresh video element in place of the current one, at the same position and settings. */
  private replaceVideo(): void {
    const old = this.els.video;
    const t = old.currentTime;
    const v = document.createElement("video");
    v.setAttribute("playsinline", "");
    v.preload = "metadata";
    const co = this.getAttribute("crossorigin");
    if (co !== null) v.crossOrigin = co === "use-credentials" ? co : "anonymous";
    const poster = this.getAttribute("poster");
    if (poster) v.poster = poster;
    old.replaceWith(v);
    this.els.video = v;
    old.removeAttribute("src");
    old.load();
    this.wireVideo(v);
    this.programme.dispose();
    this.programme = new ElementProgramme(v, { level: this.programmeLevel() });
    const src = this.getAttribute("src");
    this.videoState = src ? "loading" : "none";
    if (src) {
      v.src = src;
      if (t > 0) v.currentTime = t;
    }
    v.defaultPlaybackRate = this.speed;
    v.playbackRate = this.speed;
    this.setCaptionsTrack(this.getAttribute("captions"));
    this.applyLevels();
  }

  /** Loads the media and tracks again (a new crossorigin setting), keeping the position and play state. */
  private reloadMedia(): void {
    const { video, audio } = this.els;
    const t = video.currentTime;
    this.cancelHold();
    this.silence();
    if (this.getAttribute("src")) {
      this.reloading = true;
      this.videoState = "loading";
      video.load();
      if (t > 0) video.currentTime = t;
      if (this.want) this.playVideo();
    }
    if (this.getAttribute("described-audio")) {
      this.mixedState = "loading";
      audio.load();
    }
    this.setCaptionsTrack(this.getAttribute("captions"));
    void this.loadTrack("desc", this.getAttribute("descriptions"));
    void this.loadTrack("ext", this.getAttribute("extended"));
    this.voice.reset();
    this.prefetch(true);
  }

  // ---------------------------------------------------------------- modes

  private availability(m: PlayerMode): string | null {
    const track = (t: { state: Load }, what: string) =>
      t.state === "none"
        ? `This video has no ${what} file.`
        : t.state === "error"
          ? `The ${what} could not be loaded.`
          : t.state === "empty"
            ? `The ${what} file has no lines.`
            : null;
    if (m === "mixed") {
      if (this.mixedState === "none") return "This video has no described soundtrack.";
      if (this.mixedState === "error") return "The described soundtrack could not be loaded.";
      return null;
    }
    if (m === "extended") {
      const why = track(this.ext, "extended descriptions");
      if (why) return why;
    } else {
      const why = track(this.desc, "descriptions");
      if (why) return why;
    }
    if (speaks(m) && !this.voice.canSpeak()) return "No recorded lines, and this browser has no system voice.";
    return null;
  }

  /** The requested mode when it can run, else the first that can. */
  private pickMode(): PlayerMode | null {
    const order = [this.requested, ...MODES].filter(isMode);
    return order.find((m) => this.availability(m) === null) ?? null;
  }

  private resolveMode(): void {
    const next = this.pickMode();
    if (next === this.current) return;
    const prev = this.current;
    const lost = prev && this.availability(prev);
    this.applyMode(next);
    if (lost && prev && this.interacted) {
      this.say(`${MODE_NAME[prev]} isn’t available: ${lost.charAt(0).toLowerCase()}${lost.slice(1)} ${next ? `Using ${MODE_NAME[next].toLowerCase()}.` : ""}`.trim());
    }
  }

  private choose(m: PlayerMode, byViewer: boolean): void {
    this.requested = m;
    if (byViewer) {
      this.interacted = true;
      try {
        localStorage.setItem(STORE_KEY, m);
      } catch {
        // storage blocked: the choice lasts for this page
      }
    }
    if (speaks(m) && byViewer) this.audioCtx();
    const before = this.current;
    this.resolveMode();
    if (byViewer) {
      const why = this.availability(m);
      if (why) this.say(`${MODE_NAME[m]} isn’t available. ${why}`);
      else if (this.current === m) {
        this.say(this.modeSentence(m));
        if (speaks(m)) this.voiceNotice = false;
      }
    }
    if (before === this.current) this.renderAll();
  }

  private applyMode(next: PlayerMode | null): void {
    const { video, audio } = this.els;
    const changed = next !== this.current;
    this.cancelHold();
    this.silence();
    this.follower?.detach();
    this.follower = null;
    if (this.bufferHold) this.bufferHold = false;
    this.current = next;

    if (next === "mixed") {
      if (audio.preload !== "auto") audio.preload = "auto";
      this.follower = new MediaFollower(video, audio, { onStall: (on) => this.onMixedStall(on) });
      this.follower.attach();
    }
    const t = video.currentTime;
    this.descClock.reset(t);
    this.extClock.reset(t);
    this.applyLevels();
    if (speaks(next)) {
      this.ensureGraph();
      this.prefetch(true);
    }
    if (this.want && video.paused && !video.ended) this.playVideo();
    this.renderAll();
    if (changed) this.dispatchEvent(new CustomEvent<ModeEventDetail>("lull-modechange", { detail: { mode: next, requested: this.requested }, bubbles: true, composed: true }));
  }

  private modeSentence(m: PlayerMode): string {
    if (m === "mixed") return "Mixed audio. The described soundtrack plays.";
    if (m === "screen-reader") return "My screen reader. Each description will be announced here.";
    return `${MODE_NAME[m]}. ${this.voiceWords()}.`;
  }

  private voiceWords(): string {
    const s = this.voice.status();
    if (s === "recorded") return "Recorded voice";
    if (s === "fallback") return "Recorded voice unavailable, using system voice";
    if (s === "mixed") return "Some recordings unavailable, using system voice for those lines";
    if (s === "system") return "System voice";
    return "No voice";
  }

  /** Says once that recordings couldn't be loaded: now if the viewer is listening, else with the next play. */
  private noteVoice(): void {
    const s = this.voice.status();
    if (s === this.voiceSeen) return;
    this.voiceSeen = s;
    if (s !== "fallback" && s !== "mixed") return;
    if (speaks(this.current) && this.interacted) this.say(`${this.voiceWords()}.`);
    else this.voiceNotice = true;
  }

  private readStoredMode(): PlayerMode | null {
    try {
      const v = localStorage.getItem(STORE_KEY);
      return isMode(v) ? v : null;
    } catch {
      return null;
    }
  }

  // ---------------------------------------------------------------- tracks

  private async loadTrack(which: "desc" | "ext", url: string | null): Promise<void> {
    this.loads[which]?.abort();
    this.loads[which] = null;
    const set = (state: Load, cues: Cue[] = []) => {
      this[which] = { state, cues };
      this.rebuildCues();
    };
    if (!url) {
      set("none");
      return;
    }
    const ctl = typeof AbortController !== "undefined" ? new AbortController() : null;
    this.loads[which] = ctl;
    set("loading");
    try {
      const res = await fetch(url, { credentials: this.voice.credentials, signal: ctl?.signal });
      if (!res.ok) {
        // let the connection go: an unread error body keeps the request open
        void res.body?.cancel().catch(() => undefined);
        throw new Error(`HTTP ${res.status}`);
      }
      const track = parseVtt(await res.text());
      if (ctl?.signal.aborted) return;
      const kind: CueKind = which === "ext" ? "extended" : "standard";
      // an extended cue holds for its NOTE pause or its span, never past MAX_HOLD_S
      const cues = track.cues.map((c, index) => ({ ...c, kind, index, pause: kind === "extended" ? holdFor(c) : c.pause }));
      if (which === "desc" && track.language) this.voice.lang = track.language;
      if (this.loads[which] === ctl) this.loads[which] = null;
      set(cues.length ? "ready" : "empty", cues);
    } catch {
      if (ctl?.signal.aborted) return;
      if (this.loads[which] === ctl) this.loads[which] = null;
      set("error");
    }
  }

  private rebuildCues(): void {
    const t = this.els.video.currentTime;
    this.descClock = new CueClock(this.desc.cues);
    this.extClock = new CueClock(this.ext.cues);
    this.descClock.reset(t);
    this.extClock.reset(t);
    this.all = [...this.desc.cues, ...this.ext.cues].sort((a, b) => a.start - b.start || (a.kind === "extended" ? -1 : 1));
    this.buildSpans();
    this.buildTranscript();
    this.resolveMode();
    this.prefetch(true);
    this.renderAll();
  }

  // ---------------------------------------------------------------- playing

  private playable(): boolean {
    return !!this.getAttribute("src") && this.videoState !== "error";
  }

  private togglePlay(): void {
    if (this.want) this.userPause();
    else this.userPlay();
  }

  private userPlay(): void {
    if (!this.playable()) return;
    this.interacted = true;
    this.want = true;
    // the press is what lets audio start: wake the context now (made here for the spoken modes)
    const ctx = speaks(this.current) ? this.audioCtx() : this.ctx;
    if (ctx) void ctx.resume().then(() => this.ensureGraph(), () => undefined);
    const notice = this.voiceNotice && speaks(this.current);
    if (notice) this.voiceNotice = false;
    this.say(notice ? `Playing. ${this.voiceWords()}.` : "Playing.");
    const again = this.interrupted;
    this.interrupted = [];
    if (this.hold) {
      this.resumeHold();
    } else if (!this.startHoldIfDue(this.els.video.currentTime)) {
      this.playVideo();
      // the system voice can't be paused: lines the pause cut short are said again
      if (speaks(this.current)) for (const c of again) this.speakCue(c, c.start);
    }
    this.loop();
    this.renderAll();
  }

  private userPause(announce = true): void {
    this.want = false;
    if (this.hold) this.pauseHold();
    this.els.video.pause();
    // lines in flight wait with the picture: clips freeze with the audio clock, the system voice
    // stops and its lines are said again on play
    void this.ctx?.suspend().catch(() => undefined);
    if (!this.hold) this.interrupted = [...this.speaking].filter(([p]) => p.source === "system").map(([, c]) => c);
    this.voice.stopSpeech();
    this.stopLoop();
    if (announce) this.say("Paused.");
    this.renderAll();
  }

  private playVideo(): void {
    const p = this.els.video.play();
    if (p && typeof p.catch === "function") {
      p.catch((err: DOMException) => {
        if (err?.name === "NotAllowedError") {
          this.want = false;
          this.renderAll();
        }
      });
    }
  }

  private seekTo(t: number, announce = true): void {
    const v = this.els.video;
    if (!this.playable()) return;
    const d = Number.isFinite(v.duration) ? v.duration : Infinity;
    const to = Math.min(Math.max(0, t), d);
    this.interacted = true;
    v.currentTime = to;
    this.onSeek(to);
    if (announce) this.say(`At ${spokenTime(to)}.`);
  }

  private onSeek(t: number): void {
    this.cancelHold();
    this.silence();
    this.descClock.reset(t);
    this.extClock.reset(t);
    if (this.current === "screen-reader") {
      // landing inside a line reads it, once, rather than waiting for the next one
      const i = this.descClock.active(t);
      const cue = this.desc.cues[i];
      if (cue) {
        this.descClock.skip(i);
        const key = `${i}@${t.toFixed(3)}`;
        if (key !== this.seekAnnounced) {
          this.seekAnnounced = key;
          this.announceCue(cue);
        }
      }
    }
    this.prefetch(true, t);
    if (this.want && this.els.video.paused && !this.bufferHold && !this.els.video.ended) this.playVideo();
    this.renderAll(t);
  }

  /** Stops every line in flight and lifts the duck. */
  private silence(): void {
    this.gen++;
    this.interrupted = [];
    this.voice.stopAll();
    this.ducks = 0;
    this.programme.duck(false);
  }

  private onMixedStall(on: boolean): void {
    const v = this.els.video;
    if (on) {
      this.bufferHold = true;
      v.pause();
    } else {
      this.bufferHold = false;
      if (this.want && v.paused) this.playVideo();
    }
    this.renderAll();
  }

  // ---------------------------------------------------------------- the clock

  private loop(): void {
    if (!this.isConnected || !(this.want || this.hold)) return;
    if (!this.raf && typeof requestAnimationFrame !== "undefined") {
      this.raf = requestAnimationFrame(() => {
        this.raf = 0;
        this.tick();
        this.loop();
      });
    }
    // a timer for the next cue keeps time when frames are throttled (a background tab)
    const v = this.els.video;
    const next = Math.min(this.descClock.next(v.currentTime) ?? Infinity, this.extClock.next(v.currentTime) ?? Infinity);
    const key = Number.isFinite(next) && !v.paused ? `${next}|${v.playbackRate}` : "";
    if (key === this.timerKey) return;
    clearTimeout(this.timer);
    this.timerKey = key;
    if (!key) return;
    const ms = ((next - v.currentTime) / (v.playbackRate || 1)) * 1000;
    this.timer = setTimeout(() => {
      this.timerKey = "";
      this.tick();
      this.loop();
    }, Math.max(0, ms - 4));
  }

  private stopLoop(): void {
    if (this.raf && typeof cancelAnimationFrame !== "undefined") cancelAnimationFrame(this.raf);
    this.raf = 0;
    clearTimeout(this.timer);
    this.timerKey = "";
  }

  private tick(): void {
    const v = this.els.video;
    const now = v.currentTime;
    if (this.current === "mixed") this.follower?.check();
    if (this.want && !this.hold && !this.bufferHold && !v.paused && !v.seeking) this.fire(now);
    this.prefetch(false, now);
    this.renderTime(now);
    this.renderActive(now);
  }

  private fire(now: number): void {
    const m = this.current;
    if (m === "extended") {
      const due = this.extClock.due(now, { late: LATE_HOLD });
      if (due.length) {
        // one hold at a time; the rest fire as soon as it ends
        due.slice(1).forEach((i) => this.extClock.rearm(i));
        const cue = this.ext.cues[due[0] ?? -1];
        if (cue) {
          this.startHold(cue);
          return;
        }
      }
    }
    if (m === "spoken" || m === "extended") {
      for (const i of this.descClock.due(now, { late: LATE_SPOKEN })) {
        const cue = this.desc.cues[i];
        if (cue) this.speakCue(cue, now);
      }
    } else if (m === "screen-reader") {
      for (const i of this.descClock.due(now, { late: LATE_SPOKEN, untilEnd: true })) {
        const cue = this.desc.cues[i];
        if (cue) this.announceCue(cue);
      }
    } else if (m === "mixed") {
      this.descClock.due(now);
    }
  }

  private speakCue(cue: Cue, now: number): void {
    const g = this.gen;
    this.ensureGraph();
    this.ducks++;
    this.programme.duck(true);
    let released = false;
    const release = () => {
      if (released) return;
      released = true;
      if (g !== this.gen) return;
      this.ducks = Math.max(0, this.ducks - 1);
      if (!this.ducks) this.programme.duck(false);
      this.renderAll();
    };
    this.lastCue = cue;
    void this.voice
      .play(cue, { offset: Math.max(0, now - cue.start), current: () => g === this.gen && this.want })
      .then((p) => {
        if (g !== this.gen) {
          p.stop();
          return;
        }
        this.emitCue(cue, p.source === "none" ? "screen-reader" : p.source);
        // no voice at all: the line still reaches a screen reader
        if (p.source === "none") this.announceCue(cue, false);
        this.speaking.set(p, cue);
        void p.done.then(() => {
          this.speaking.delete(p);
          release();
        });
        if (p.source === "none") release();
      }, release);
    this.renderAll();
  }

  private announceCue(cue: Cue, emit = true): void {
    this.lastCue = cue;
    this.announce("cue", cue.text);
    if (emit) this.emitCue(cue, "screen-reader");
    this.renderAll();
  }

  private emitCue(cue: Cue, via: CueEventDetail["via"], hold?: number): void {
    if (!this.current) return;
    const detail: CueEventDetail = { id: cue.id, text: cue.text, start: cue.start, kind: cue.kind, mode: this.current, via };
    if (hold !== undefined) detail.hold = hold;
    this.dispatchEvent(new CustomEvent<CueEventDetail>("lull-cue", { detail, bubbles: true, composed: true }));
  }

  // ---------------------------------------------------------------- extended holds

  private startHoldIfDue(now: number): boolean {
    if (this.current !== "extended") return false;
    const due = this.extClock.due(now, { late: LATE_HOLD });
    due.slice(1).forEach((i) => this.extClock.rearm(i));
    const cue = this.ext.cues[due[0] ?? -1];
    if (!cue) return false;
    this.startHold(cue);
    return true;
  }

  private startHold(cue: Cue): void {
    const v = this.els.video;
    // the clip plays at the video's rate, so the wait shortens (or lengthens) with it, still bounded
    const pause = holdWall(cue, this.speed);
    this.hold = { cue, remaining: pause * 1000, since: 0, timer: undefined, cap: undefined, playback: null, timeUp: false, voiceDone: false, respeak: false };
    v.pause();
    // land on the frame the line belongs to
    if (Math.abs(v.currentTime - cue.start) > 0.05) v.currentTime = cue.start;
    this.lastCue = cue;
    this.armHold();
    this.speakHold(true);
    this.renderAll();
  }

  private armHold(): void {
    const h = this.hold;
    if (!h) return;
    clearTimeout(h.timer);
    h.since = Date.now();
    if (h.timeUp) return;
    h.timer = setTimeout(() => {
      h.timeUp = true;
      h.cap = setTimeout(() => {
        h.voiceDone = true;
        this.maybeEndHold();
      }, HOLD_CAP_MS);
      this.maybeEndHold();
    }, Math.max(0, h.remaining));
  }

  private speakHold(first = false): void {
    const h = this.hold;
    if (!h) return;
    const g = this.gen;
    h.voiceDone = false;
    h.respeak = false;
    void this.voice.play(h.cue, { current: () => g === this.gen && this.hold === h && this.want }).then((p) => {
      if (this.hold !== h || g !== this.gen) {
        p.stop();
        return;
      }
      if (p.source === "none" && !this.want) {
        // paused before the line could start: say it when play resumes
        h.respeak = true;
        return;
      }
      h.playback = p;
      if (first) this.emitCue(h.cue, p.source === "none" ? "screen-reader" : p.source, holdFor(h.cue));
      if (p.source === "none") this.announceCue(h.cue, false);
      void p.done.then(() => {
        if (this.hold !== h || h.playback !== p) return;
        h.voiceDone = true;
        this.maybeEndHold();
      });
    });
  }

  private maybeEndHold(): void {
    const h = this.hold;
    if (!h || !h.timeUp || !h.voiceDone) return;
    this.endHold();
  }

  private endHold(): void {
    const h = this.hold;
    if (!h) return;
    clearTimeout(h.timer);
    clearTimeout(h.cap);
    this.hold = null;
    if (this.want) {
      this.playVideo();
      this.loop();
    }
    this.renderAll();
  }

  private pauseHold(): void {
    const h = this.hold;
    if (!h) return;
    clearTimeout(h.timer);
    clearTimeout(h.cap);
    if (!h.timeUp) h.remaining -= Date.now() - h.since;
    if (h.playback?.source === "system") {
      // the system voice can't be paused reliably: say the line again on resume
      const p = h.playback;
      h.playback = null;
      h.respeak = true;
      p.stop();
    }
  }

  private resumeHold(): void {
    const h = this.hold;
    if (!h) return;
    void this.ctx?.resume().catch(() => undefined);
    if (h.respeak) this.speakHold();
    if (h.timeUp) {
      h.cap = setTimeout(() => {
        h.voiceDone = true;
        this.maybeEndHold();
      }, HOLD_CAP_MS);
    }
    this.armHold();
    this.maybeEndHold();
  }

  private cancelHold(): void {
    const h = this.hold;
    if (!h) return;
    clearTimeout(h.timer);
    clearTimeout(h.cap);
    h.playback?.stop();
    this.hold = null;
  }

  // ---------------------------------------------------------------- audio

  private audioCtx(): AudioContext | null {
    if (this.ctx) return this.ctx;
    const AC: typeof AudioContext | undefined =
      typeof window !== "undefined" ? (window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext) : undefined;
    if (!AC) return null;
    try {
      this.ctx = new AC();
    } catch {
      this.ctx = null;
    }
    return this.ctx;
  }

  /** Routing through Web Audio needs media the page may read: same origin, or CORS asked for. */
  private graphAllowed(): boolean {
    const src = this.els.video.currentSrc || this.getAttribute("src") || "";
    if (!src) return false;
    if (this.hasAttribute("crossorigin")) return true;
    try {
      return new URL(src, document.baseURI).origin === location.origin;
    } catch {
      return false;
    }
  }

  /** Moves the programme onto the audio graph once the context runs. One-way, per element. */
  private ensureGraph(): void {
    if (this.programme.kind === "graph" || !speaks(this.current) || !this.ctx || this.ctx.state !== "running" || !this.graphAllowed()) return;
    const old = this.programme;
    old.dispose();
    try {
      this.programme = new GraphProgramme(this.els.video, this.ctx, { level: this.programmeLevel(), ducked: old.ducked });
    } catch {
      this.programme = old;
    }
  }

  private programmeLevel(): number {
    return this.current === "mixed" || this.muted ? 0 : this.volume;
  }

  private applyLevels(): void {
    const { video, audio } = this.els;
    video.muted = this.current === "mixed" || this.muted;
    this.programme.setLevel(this.programmeLevel());
    audio.volume = this.volume;
    audio.muted = this.muted;
    this.voice.setVolume(this.muted ? 0 : this.volume);
  }

  private prefetch(force: boolean, at = this.els.video.currentTime): void {
    if (!speaks(this.current) || !this.voice.base) return;
    if (!force && Math.abs(at - this.lastPrefetch) < 1) return;
    this.lastPrefetch = at;
    const near = (c: Cue) => c.start >= at - 1 && c.start <= at + PREFETCH_AHEAD;
    const cues = [...this.desc.cues.filter(near), ...(this.current === "extended" ? this.ext.cues.filter(near) : [])];
    // always learn early whether recordings exist at all
    if (!cues.length && this.desc.cues[0]) cues.push(this.desc.cues[0]);
    this.voice.prefetch(cues.map((c) => c.id));
  }

  // ---------------------------------------------------------------- controls

  private setVolume(v: number, muted: boolean, announce: boolean): void {
    this.volume = Math.min(1, Math.max(0, Math.round(v * 100) / 100));
    this.muted = muted || this.volume === 0;
    if (!this.muted && this.volume === 0) this.volume = VOLUME_STEP;
    this.applyLevels();
    if (announce) this.say(this.muted ? "Muted." : `Volume ${Math.round(this.volume * 100)}%.`);
    this.renderAll();
  }

  private cycleSpeed(dir: 1 | -1, wrap = true): void {
    const i = SPEEDS.indexOf(this.speed as (typeof SPEEDS)[number]);
    let j = (i < 0 ? 1 : i) + dir;
    if (wrap) j = (j + SPEEDS.length) % SPEEDS.length;
    j = Math.min(SPEEDS.length - 1, Math.max(0, j));
    this.speed = SPEEDS[j] ?? 1;
    this.els.video.playbackRate = this.speed;
    this.say(`Speed ${this.speed} times.`);
    this.renderAll();
  }

  private setCaptionsTrack(url: string | null): void {
    const { video } = this.els;
    video.querySelector("track")?.remove();
    if (url) {
      const t = document.createElement("track");
      t.kind = "captions";
      t.label = "Captions";
      t.srclang = this.voice.lang || "en";
      t.src = url;
      video.appendChild(t);
      if (t.track) t.track.mode = this.captionsOn ? "showing" : "hidden";
    } else {
      this.captionsOn = false;
    }
  }

  private toggleCaptions(): void {
    const t = this.els.video.querySelector("track");
    if (!t?.track) {
      this.say("This video has no captions.");
      return;
    }
    this.captionsOn = !this.captionsOn;
    t.track.mode = this.captionsOn ? "showing" : "hidden";
    this.say(this.captionsOn ? "Captions on." : "Captions off.");
    this.renderAll();
  }

  private menuOpen(): boolean {
    return !this.els.menu.hidden;
  }

  private toggleMenu(open: boolean, moveFocus: boolean): void {
    const { menu, mode, group } = this.els;
    menu.hidden = !open;
    mode.setAttribute("aria-expanded", String(open));
    if (open) document.addEventListener("pointerdown", this.onOutside, true);
    else document.removeEventListener("pointerdown", this.onOutside, true);
    if (open && moveFocus) {
      const target = group.querySelector<HTMLInputElement>("input:checked:not(:disabled)") ?? group.querySelector<HTMLInputElement>("input:not(:disabled)");
      (target ?? mode).focus();
    } else if (!open && moveFocus) {
      mode.focus();
    }
  }

  private togglePanel(which: "transcript" | "keys"): void {
    const panel = which === "transcript" ? this.els.transcript : this.els.keymap;
    const button = which === "transcript" ? this.els.tx : this.els.keys;
    panel.hidden = !panel.hidden;
    button.setAttribute("aria-expanded", String(!panel.hidden));
  }

  private fullscreenElement(): Element | null {
    const d = document as Document & { webkitFullscreenElement?: Element | null };
    return d.fullscreenElement ?? d.webkitFullscreenElement ?? null;
  }

  private toggleFullscreen(): void {
    const d = document as Document & { webkitExitFullscreen?: () => void };
    const host = this as HTMLElement & { webkitRequestFullscreen?: () => void };
    const video = this.els.video as HTMLVideoElement & { webkitEnterFullscreen?: () => void };
    if (this.fullscreenElement() === this) {
      if (d.exitFullscreen) void d.exitFullscreen().catch(() => undefined);
      else d.webkitExitFullscreen?.();
    } else if (host.requestFullscreen) {
      void host.requestFullscreen().catch(() => this.say("Full screen isn’t available here."));
    } else if (host.webkitRequestFullscreen) {
      host.webkitRequestFullscreen();
    } else if (video.webkitEnterFullscreen) {
      video.webkitEnterFullscreen();
    }
  }

  private onKey(e: KeyboardEvent): void {
    if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
    const t = e.composedPath()[0] as HTMLElement | undefined;
    const isRadio = t instanceof HTMLInputElement && t.type === "radio";
    const onVolume = t === this.els.volume;
    const onSeek = t === this.els.seek;
    const onButton = t instanceof HTMLButtonElement && t !== this.els.play;
    const key = e.key;
    const handled = () => {
      e.preventDefault();
      e.stopPropagation();
    };
    switch (key) {
      case " ":
      case "Spacebar":
        if (onButton || isRadio) return;
        handled();
        this.togglePlay();
        return;
      case "k":
      case "K":
        handled();
        this.togglePlay();
        return;
      case "ArrowLeft":
      case "ArrowRight":
        if (isRadio || onVolume) return;
        handled();
        this.seekTo(this.els.video.currentTime + (key === "ArrowLeft" ? -SEEK_STEP : SEEK_STEP));
        return;
      case "ArrowUp":
      case "ArrowDown":
        if (isRadio) return;
        handled();
        this.setVolume(this.volume + (key === "ArrowUp" ? VOLUME_STEP : -VOLUME_STEP), false, true);
        return;
      case "Home":
      case "End":
        if (!onSeek) return;
        handled();
        this.seekTo(key === "Home" ? 0 : this.els.video.duration || 0);
        return;
      case "m":
      case "M":
        handled();
        this.toggleMenu(!this.menuOpen(), true);
        return;
      case "c":
      case "C":
        handled();
        this.toggleCaptions();
        return;
      case "t":
      case "T":
        handled();
        this.togglePanel("transcript");
        return;
      case "f":
      case "F":
        handled();
        this.toggleFullscreen();
        return;
      case "?":
        handled();
        this.togglePanel("keys");
        return;
      case "<":
      case ">":
        handled();
        this.cycleSpeed(key === "<" ? -1 : 1, false);
        return;
      case "Escape":
        if (this.menuOpen()) {
          handled();
          this.toggleMenu(false, true);
        }
        return;
    }
  }

  // ---------------------------------------------------------------- live regions

  private say(text: string): void {
    this.announce("status", text);
  }

  /** Clears, then writes on the next turn, so the same words said twice are heard twice. */
  private announce(which: "status" | "cue", text: string): void {
    const el = which === "status" ? this.els.liveStatus : this.els.liveCue;
    clearTimeout(this.announceTimer[which]);
    el.textContent = "";
    this.announceTimer[which] = setTimeout(() => {
      el.textContent = text;
    }, 30);
  }

  // ---------------------------------------------------------------- building

  private buildMenu(): void {
    const { group } = this.els;
    for (const m of MODES) {
      const label = document.createElement("label");
      label.className = "opt";
      label.dataset.mode = m;
      const input = document.createElement("input");
      input.type = "radio";
      input.name = "mode";
      input.value = m;
      input.setAttribute("aria-labelledby", `n-${m}`);
      input.setAttribute("aria-describedby", `d-${m} r-${m}`);
      const name = document.createElement("span");
      name.className = "o-name";
      name.id = `n-${m}`;
      name.textContent = MODE_NAME[m];
      const mark = document.createElement("span");
      mark.className = "o-mark";
      mark.innerHTML = ICON.ok;
      const desc = document.createElement("span");
      desc.className = "o-desc";
      desc.id = `d-${m}`;
      desc.textContent = MODE_DESC[m];
      const why = document.createElement("span");
      why.className = "o-why";
      why.id = `r-${m}`;
      label.append(input, name, mark, desc, why);
      group.append(label);
    }
  }

  private buildSpans(): void {
    const { spans, video } = this.els;
    const d = video.duration;
    spans.textContent = "";
    this.spanEls.clear();
    if (!Number.isFinite(d) || d <= 0) return;
    const pct = (s: number) => `${Math.min(100, Math.max(0, (s / d) * 100))}%`;
    for (const c of this.desc.cues) {
      const i = document.createElement("i");
      i.style.left = pct(c.start);
      i.style.width = pct(Math.max(0, Math.min(c.end, d) - c.start));
      spans.append(i);
      this.spanEls.set(c, i);
    }
    for (const c of this.ext.cues) {
      const b = document.createElement("b");
      b.style.left = pct(c.start);
      spans.append(b);
      this.spanEls.set(c, b);
    }
    this.activeKey = "";
  }

  private buildTranscript(): void {
    const { lines } = this.els;
    lines.textContent = "";
    this.rowEls.clear();
    for (const c of this.all) {
      const li = document.createElement("li");
      const row = document.createElement("button");
      row.type = "button";
      row.className = "row";
      row.setAttribute("data-at", String(c.start));
      const tc = document.createElement("span");
      tc.className = "r-tc";
      tc.textContent = clock(c.start);
      const ln = document.createElement("span");
      ln.className = "r-ln";
      ln.textContent = c.text;
      row.append(tc, ln);
      if (c.kind === "extended") {
        const x = document.createElement("span");
        x.className = "r-x";
        x.innerHTML = ICON.extended;
        x.append("Extended");
        const w = document.createElement("span");
        w.className = "w";
        w.textContent = `video waits ${seconds2(c.pause ?? 0)}`;
        x.append(" ", w);
        row.append(x);
      }
      li.append(row);
      lines.append(li);
      this.rowEls.set(c, row);
    }
    this.activeKey = "";
  }

  // ---------------------------------------------------------------- rendering

  private isActive(c: Cue): boolean {
    if (this.hold?.cue === c) return true;
    const t = this.els.video.currentTime;
    return c.start <= t + 0.02 && t < c.end;
  }

  private renderAll(at?: number): void {
    const { player, band, play, seek, modeName, group, speed, speedValue, cc, mute, volume, fs, meta, notice, nTitle, nBody, nUrl, count, empty, lines, video } = this.els;
    const playable = this.playable();
    const m = this.current;
    player.dataset.mode = m ?? "none";
    // nothing will ever be said here without a mode
    band.hidden = !m;

    // the picture, or why there is none
    const src = this.getAttribute("src");
    const missing = !src ? "none" : this.videoState === "error" ? "error" : null;
    notice.hidden = !missing;
    if (missing) {
      nTitle.textContent = missing === "none" ? "No video to play" : "This video isn’t available";
      const hasLines = this.all.length > 0;
      nBody.textContent =
        missing === "none"
          ? "This player has no video source."
          : `The video file could not be loaded.${hasLines ? " Its descriptions are still in the transcript." : ""}`;
      nUrl.textContent = missing === "error" ? (src ?? "") : "";
      nUrl.hidden = missing !== "error";
    }

    // play
    play.disabled = !playable;
    const label = this.want ? "Pause" : video.ended ? "Play again" : "Play";
    const icon = this.want ? ICON.pause : video.ended ? ICON.replay : ICON.play;
    if (play.dataset.label !== label) {
      play.dataset.label = label;
      play.innerHTML = `${icon}<span class="sr-only"></span>`;
      (play.lastElementChild as HTMLElement).textContent = label;
    }
    seek.disabled = !playable || this.videoState !== "ready";

    // mode button and menu
    modeName.textContent = m ? MODE_NAME[m] : "No descriptions";
    for (const opt of group.querySelectorAll<HTMLLabelElement>(".opt")) {
      const om = opt.dataset.mode as PlayerMode;
      const input = opt.querySelector("input") as HTMLInputElement;
      const why = this.availability(om);
      input.checked = om === m;
      input.disabled = !!why;
      opt.toggleAttribute("data-checked", om === m);
      opt.toggleAttribute("data-off", !!why);
      const whyEl = opt.querySelector(".o-why") as HTMLElement;
      whyEl.textContent = why ? `Not available. ${why}` : speaks(om) && om === m ? `${this.voiceWords()}.` : "";
      whyEl.hidden = !whyEl.textContent;
    }

    // speed, captions, volume, full screen
    speedValue.textContent = `${this.speed}×`;
    const track = video.querySelector("track");
    cc.hidden = !track;
    // without a picture these have nothing to act on; the transcript and the keys still do
    speed.disabled = !playable;
    mute.disabled = !playable;
    volume.disabled = !playable;
    fs.disabled = !playable;
    cc.setAttribute("aria-pressed", String(this.captionsOn));
    mute.setAttribute("aria-label", this.muted ? "Unmute" : "Mute");
    mute.setAttribute("aria-pressed", String(this.muted));
    const muteIcon = this.muted ? "muted" : "voice";
    if (mute.dataset.icon !== muteIcon) {
      mute.dataset.icon = muteIcon;
      mute.innerHTML = ICON[muteIcon];
    }
    const shown = this.muted ? 0 : this.volume;
    if (Number(volume.value) !== shown) volume.value = String(shown);
    volume.setAttribute("aria-valuetext", this.muted ? "Muted" : `${Math.round(this.volume * 100)}%`);
    volume.style.setProperty("--lp-v", `${shown * 100}%`);
    const full = this.fullscreenElement() === this;
    fs.setAttribute("aria-label", full ? "Exit full screen" : "Full screen");
    const fsIcon = full ? "exit" : "enter";
    if (fs.dataset.icon !== fsIcon) {
      fs.dataset.icon = fsIcon;
      fs.innerHTML = ICON[fsIcon];
    }

    // what is speaking, under the controls
    meta.textContent = "";
    const item = (text: string, icon?: string) => {
      const s = document.createElement("span");
      if (icon) s.innerHTML = icon;
      s.append(text);
      meta.append(s);
    };
    const lineCount = (n: number, what = "line") => `${n} ${what}${n === 1 ? "" : "s"}`;
    if (this.desc.state === "loading" || this.ext.state === "loading") item("Loading descriptions");
    else if (m === "mixed") item("Described soundtrack", ICON.voice);
    else if (m === "screen-reader") item("Read by your screen reader");
    else if (speaks(m)) item(this.voiceWords(), ICON.voice);
    else if (!m) item(this.availability("spoken") ?? "This video has no descriptions.");
    if (m && m !== "extended" && this.desc.cues.length) item(lineCount(this.desc.cues.length));
    if (m === "extended" && this.ext.cues.length) item(lineCount(this.ext.cues.length, "extended line"));
    if (this.bufferHold) item("Waiting for the described soundtrack");

    // transcript
    count.textContent = this.all.length ? lineCount(this.all.length) : "";
    lines.hidden = !this.all.length;
    const descWhy = this.desc.state === "error" ? "The descriptions could not be loaded." : this.desc.state === "loading" ? "Loading descriptions." : "This video has no descriptions.";
    empty.hidden = this.all.length > 0;
    empty.textContent = this.all.length ? "" : descWhy;

    this.renderTime(at);
    this.renderActive(at);
  }

  private renderTime(at?: number): void {
    const { video, seek, track, now, dur } = this.els;
    const t = at ?? video.currentTime;
    const d = Number.isFinite(video.duration) ? video.duration : 0;
    // a focused slider reads its value aloud when it changes: hold it still while the video plays
    // under it, and let it move when the viewer moves it or pauses
    const focused = this.shadowRoot?.activeElement === seek;
    const quiet = focused && !video.paused && !video.seeking;
    const key = `${t.toFixed(2)}|${d}|${quiet}`;
    if (key === this.timeKey) return;
    this.timeKey = key;
    const nowText = clock(t);
    if (now.textContent !== nowText) now.textContent = nowText;
    const durText = d ? clock(d) : "0:00";
    if (dur.textContent !== durText) dur.textContent = durText;
    track.style.setProperty("--lp-p", d ? String(Math.min(1, t / d)) : "0");
    if (quiet) return;
    if (seek.max !== String(d)) seek.max = String(d);
    seek.value = String(t);
    seek.setAttribute("aria-valuetext", d ? `${spokenTime(t)} of ${spokenTime(d)}` : spokenTime(t));
  }

  private renderActive(at?: number): void {
    const { band, line, hold, holdWait, idle } = this.els;
    const t = at ?? this.els.video.currentTime;
    const live = this.hold?.cue ?? this.all.find((c) => c.kind === "standard" && c.start <= t + 0.02 && t < c.end) ?? null;
    if (live) this.lastCue = live;
    const shownCue = live ?? (this.lastCue && this.lastCue.start <= t ? this.lastCue : null);
    const key = `${live ? `${live.kind}${live.index}` : "-"}|${shownCue ? `${shownCue.kind}${shownCue.index}` : "-"}|${this.current}|${this.hold ? 1 : 0}`;
    if (key === this.activeKey) return;
    this.activeKey = key;

    band.dataset.state = live ? "live" : shownCue ? "rest" : "idle";
    line.hidden = !shownCue;
    line.textContent = shownCue?.text ?? "";
    idle.hidden = !!shownCue;
    idle.textContent = this.current ? IDLE[this.current] : "This video has no descriptions.";
    hold.hidden = !this.hold;
    if (this.hold) holdWait.textContent = `The video waits ${seconds2(holdWall(this.hold.cue, this.speed))} for this line`;

    for (const [c, el] of this.spanEls) el.classList.toggle("on", c === live);
    for (const [c, row] of this.rowEls) {
      if (c === live) row.setAttribute("aria-current", "true");
      else row.removeAttribute("aria-current");
    }
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "lull-player": LullPlayer;
  }
}

if (typeof customElements !== "undefined" && !customElements.get("lull-player")) {
  customElements.define("lull-player", LullPlayer);
}
