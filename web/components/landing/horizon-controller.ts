/**
 * The one player state behind the hero: the gate's lit door and the horizon's transport
 * both drive it. Coarse state (what the buttons say) is a snapshot for useSyncExternalStore;
 * the clock `t` changes every frame and is read by the canvas loop, never rendered by React.
 */
export type Track = "original" | "described";

export type PlayerSnapshot = {
  /** visual: the 3x timing preview, no sound. audio: real sound at 1x. */
  mode: "visual" | "audio";
  playing: boolean;
  started: boolean;
  ended: boolean;
  track: Track;
};

const INITIAL: PlayerSnapshot = { mode: "visual", playing: false, started: false, ended: false, track: "described" };

/** How much faster than real time the silent preview runs. */
export const PREVIEW_RATE = 3;

export class HorizonController {
  /** Playhead, seconds. */
  t = 0;
  /** Index of the cue last announced, so each is announced once per pass. */
  spoken = -1;
  readonly duration: number;

  private snap: PlayerSnapshot = INITIAL;
  private listeners = new Set<() => void>();
  private audio: Partial<Record<Track, HTMLAudioElement>> = {};
  private kick: () => void = () => {};

  constructor(duration: number) {
    this.duration = duration;
  }

  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  };
  getSnapshot = () => this.snap;
  getServerSnapshot = () => INITIAL;

  private set(patch: Partial<PlayerSnapshot>) {
    this.snap = { ...this.snap, ...patch };
    this.listeners.forEach((fn) => fn());
  }

  /** The horizon registers its frame loop here; every state change draws at least one frame. */
  attachLoop(kick: () => void) {
    this.kick = kick;
    return () => {
      if (this.kick === kick) this.kick = () => {};
    };
  }

  /** Both soundtracks are loaded: switch to real sound. */
  attachAudio(tracks: Record<Track, HTMLAudioElement>) {
    this.audio = tracks;
    this.set({ mode: "audio" });
    return () => {
      Object.values(tracks).forEach((a) => a.pause());
      this.audio = {};
      this.set({ mode: "visual" });
    };
  }

  activeAudio(): HTMLAudioElement | undefined {
    return this.snap.mode === "audio" ? this.audio[this.snap.track] : undefined;
  }

  play = () => {
    if (this.snap.ended) {
      this.t = 0;
      this.spoken = -1;
    }
    this.set({ started: true, playing: true, ended: false });
    const a = this.activeAudio();
    if (a) {
      a.currentTime = this.t;
      a.play().catch(() => this.set({ playing: false }));
    }
    this.kick();
  };

  pause = () => {
    Object.values(this.audio).forEach((a) => a.pause());
    this.set({ playing: false });
    this.kick();
  };

  toggle = () => (this.snap.playing ? this.pause() : this.play());

  replay = () => {
    this.t = 0;
    this.spoken = -1;
    this.set({ ended: false });
    this.play();
  };

  /** The clock reached the end of the clip. */
  finish = () => {
    this.t = this.duration;
    Object.values(this.audio).forEach((a) => a.pause());
    this.set({ playing: false, ended: true });
    this.kick();
  };

  /** Original or described soundtrack; keeps the position and the play state. */
  setTrack = (track: Track) => {
    if (track === this.snap.track) return;
    const wasPlaying = this.snap.playing;
    this.activeAudio()?.pause();
    this.set({ track });
    const a = this.activeAudio();
    if (a) {
      a.currentTime = this.t;
      if (wasPlaying) a.play().catch(() => this.set({ playing: false }));
    }
  };
}
