/**
 * Keeps one media element (the described audio) in step with another (the video).
 *
 * The follower mirrors the leader's play, pause, seek and rate. On each `check()` it
 * measures the gap between the two clocks and jumps the follower when the gap is larger
 * than the threshold (120 ms by default). When the follower runs out of data while the
 * leader plays, `onStall(true)` asks the owner to hold the leader, and `onStall(false)`
 * says it may go on: the mixed track is the only sound in that mode, so the picture must
 * not run ahead of it.
 */
export type FollowerOptions = {
  /** Seconds of drift tolerated before the follower jumps. */
  threshold?: number;
  onStall?: (stalled: boolean) => void;
};

type Media = HTMLMediaElement;
const HAVE_FUTURE_DATA = 3;

export class MediaFollower {
  readonly leader: Media;
  readonly follower: Media;
  readonly threshold: number;
  /** How many times the follower was moved to correct drift. */
  corrections = 0;
  private stalled = false;
  private attached = false;
  private readonly onStall?: (stalled: boolean) => void;
  private readonly off: (() => void)[] = [];

  constructor(leader: Media, follower: Media, { threshold = 0.12, onStall }: FollowerOptions = {}) {
    this.leader = leader;
    this.follower = follower;
    this.threshold = threshold;
    this.onStall = onStall;
  }

  get isStalled(): boolean {
    return this.stalled;
  }

  attach(): void {
    if (this.attached) return;
    this.attached = true;
    const { leader: l, follower: f } = this;
    const on = (el: Media, type: string, fn: () => void) => {
      el.addEventListener(type, fn);
      this.off.push(() => el.removeEventListener(type, fn));
    };
    on(l, "play", () => this.playFollower());
    on(l, "playing", () => this.playFollower());
    on(l, "pause", () => {
      if (!this.stalled) f.pause();
    });
    on(l, "waiting", () => f.pause());
    on(l, "ended", () => f.pause());
    on(l, "seeking", () => this.align());
    on(l, "ratechange", () => {
      f.playbackRate = l.playbackRate;
    });
    on(f, "waiting", () => {
      if (!l.paused && !l.ended) this.setStalled(true);
    });
    const recover = () => {
      if (this.stalled && f.readyState >= HAVE_FUTURE_DATA) this.setStalled(false);
    };
    on(f, "canplay", recover);
    on(f, "canplaythrough", recover);
    on(f, "playing", recover);
    on(f, "error", () => this.setStalled(false));

    f.playbackRate = l.playbackRate;
    this.align();
    if (!l.paused) this.playFollower();
  }

  detach(): void {
    if (!this.attached) return;
    this.attached = false;
    this.off.splice(0).forEach((fn) => fn());
    this.follower.pause();
    if (this.stalled) this.setStalled(false);
  }

  /** Corrects drift; returns the gap in seconds before any correction (follower minus leader). */
  check(): number {
    const { leader: l, follower: f } = this;
    if (!this.attached) return 0;
    const drift = f.currentTime - l.currentTime;
    // past the end of a shorter follower there is nothing to align
    if (Number.isFinite(f.duration) && l.currentTime >= f.duration) return drift;
    if (!l.paused && f.paused && !this.stalled) this.playFollower();
    if (Math.abs(drift) > this.threshold && !l.seeking) {
      this.corrections++;
      this.align();
    }
    return drift;
  }

  private align(): void {
    const t = this.leader.currentTime;
    if (Math.abs(this.follower.currentTime - t) > 0.001) this.follower.currentTime = t;
  }

  private playFollower(): void {
    const { leader: l, follower: f } = this;
    if (l.paused || !f.paused) return;
    this.align();
    const p = f.play();
    // a refused play (autoplay rules, a missing file) is reported through the element's own events
    if (p && typeof p.catch === "function") p.catch(() => undefined);
  }

  private setStalled(on: boolean): void {
    if (this.stalled === on) return;
    this.stalled = on;
    this.onStall?.(on);
    // the owner chose not to resume (the viewer paused meanwhile): don't let the follower run alone
    if (!on && this.leader.paused) this.follower.pause();
  }
}
