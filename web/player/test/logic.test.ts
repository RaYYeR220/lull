import { afterEach, describe, expect, it, vi } from "vitest";
import { CueClock, MAX_HOLD_S, holdFor } from "../src/clock";
import { DUCK_DB, ElementProgramme, GraphProgramme, RAMP_S, dbToGain } from "../src/duck";
import { MediaFollower } from "../src/sync";
import { clock, spokenTime } from "../src/time";
import { clipUrls } from "../src/voice";

describe("CueClock", () => {
  const cues = [
    { start: 1, end: 2 },
    { start: 3.6, end: 4.6 },
    { start: 10, end: 12 },
  ];

  it("fires each cue once, when the playhead reaches its start", () => {
    const c = new CueClock(cues);
    expect(c.due(0.5)).toEqual([]);
    expect(c.due(0.99)).toEqual([0]); // 20 ms early absorbs timer jitter
    expect(c.due(1.2)).toEqual([]);
    expect(c.due(3.65)).toEqual([1]);
    expect(c.next(3.65)).toBe(10);
  });

  it("drops a cue passed long ago instead of speaking it late", () => {
    const c = new CueClock(cues);
    expect(c.due(2.5)).toEqual([]);
    expect(c.due(3.7)).toEqual([1]);
  });

  it("lets a screen reader line through while the playhead is inside it", () => {
    const c = new CueClock(cues);
    expect(c.due(1.9, { untilEnd: true })).toEqual([0]);
  });

  it("after a seek, a cue starting exactly there is due and earlier ones are passed", () => {
    const c = new CueClock(cues);
    c.reset(3.6);
    expect(c.due(3.6)).toEqual([1]);
    c.reset(0);
    expect(c.due(1)).toEqual([0]);
    c.reset(5);
    expect(c.next(5)).toBe(10);
  });

  it("skips a cue that was read out another way (a seek into it)", () => {
    const c = new CueClock(cues);
    c.reset(3.6);
    c.skip(1);
    expect(c.due(3.7)).toEqual([]);
    expect(c.next(3.7)).toBe(10);
  });

  it("rearms a cue and knows which one is active", () => {
    const c = new CueClock(cues);
    expect(c.due(1)).toEqual([0]);
    c.rearm(0);
    expect(c.due(1.1)).toEqual([0]);
    expect(c.active(1.5)).toBe(0);
    expect(c.active(2.5)).toBe(-1);
  });
});

describe("dbToGain and the duck", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("ducks by 10 dB", () => {
    expect(DUCK_DB).toBe(-10);
    expect(dbToGain(-10)).toBeCloseTo(0.3162, 4);
    expect(dbToGain(0)).toBe(1);
  });

  it("ramps an element's volume over 200 ms and back", () => {
    let now = 0;
    const ticks: (() => void)[] = [];
    const clockStub = {
      now: () => now,
      every: (fn: () => void) => {
        ticks.push(fn);
        return () => ticks.splice(ticks.indexOf(fn), 1);
      },
    };
    const media = { volume: 1 };
    const p = new ElementProgramme(media, { level: 0.8, clock: clockStub });
    expect(media.volume).toBeCloseTo(0.8, 6);
    p.duck(true);
    expect(p.ducked).toBe(true);
    now = (RAMP_S * 1000) / 2;
    ticks.forEach((t) => t());
    expect(media.volume).toBeCloseTo(0.8 * (1 + (dbToGain(-10) - 1) / 2), 4);
    now = RAMP_S * 1000;
    [...ticks].forEach((t) => t());
    expect(media.volume).toBeCloseTo(0.8 * dbToGain(-10), 4);
    expect(ticks).toHaveLength(0);
    p.setLevel(0.5);
    expect(media.volume).toBeCloseTo(0.5 * dbToGain(-10), 4);
    p.duck(false);
    now += RAMP_S * 1000;
    [...ticks].forEach((t) => t());
    expect(media.volume).toBeCloseTo(0.5, 6);
  });

  it("schedules a linear gain ramp on the audio clock", () => {
    const calls: [string, number, number?][] = [];
    const gain = {
      value: 1,
      cancelScheduledValues: (t: number) => calls.push(["cancel", t]),
      setValueAtTime: (v: number, t: number) => calls.push(["set", v, t]),
      linearRampToValueAtTime: (v: number, t: number) => calls.push(["ramp", v, t]),
    };
    const ctx = { currentTime: 10 } as unknown as AudioContext;
    const p = new GraphProgramme({} as HTMLMediaElement, ctx, { level: 1, node: { gain } });
    p.duck(true);
    expect(calls).toEqual([
      ["cancel", 10],
      ["set", 1, 10],
      ["ramp", dbToGain(-10), 10 + RAMP_S],
    ]);
    calls.length = 0;
    p.duck(true);
    expect(calls).toEqual([]); // already ducked
  });
});

/** Enough of a media element for the follower: clocks, play/pause, events. */
class FakeMedia extends EventTarget {
  currentTime = 0;
  paused = true;
  ended = false;
  seeking = false;
  playbackRate = 1;
  readyState = 4;
  duration = 6;
  play() {
    this.paused = false;
    this.dispatchEvent(new Event("play"));
    return Promise.resolve();
  }
  pause() {
    if (this.paused) return;
    this.paused = true;
    this.dispatchEvent(new Event("pause"));
  }
}

describe("MediaFollower", () => {
  const pair = () => {
    const video = new FakeMedia();
    const audio = new FakeMedia();
    const stalls: boolean[] = [];
    const f = new MediaFollower(video as unknown as HTMLMediaElement, audio as unknown as HTMLMediaElement, { onStall: (s) => stalls.push(s) });
    f.attach();
    return { video, audio, f, stalls };
  };

  it("mirrors play, pause, seek and rate", () => {
    const { video, audio } = pair();
    video.currentTime = 2;
    video.play();
    expect(audio.paused).toBe(false);
    expect(audio.currentTime).toBe(2);
    video.currentTime = 4;
    video.dispatchEvent(new Event("seeking"));
    expect(audio.currentTime).toBe(4);
    video.playbackRate = 1.25;
    video.dispatchEvent(new Event("ratechange"));
    expect(audio.playbackRate).toBe(1.25);
    video.pause();
    expect(audio.paused).toBe(true);
  });

  it("leaves drift under 120 ms alone and corrects more", () => {
    const { video, audio, f } = pair();
    video.play();
    video.currentTime = 3;
    audio.currentTime = 3.1;
    expect(f.check()).toBeCloseTo(0.1, 6);
    expect(audio.currentTime).toBe(3.1);
    expect(f.corrections).toBe(0);
    audio.currentTime = 2.8;
    expect(f.check()).toBeCloseTo(-0.2, 6);
    expect(audio.currentTime).toBe(3);
    expect(f.corrections).toBe(1);
  });

  it("asks to hold the video while the track buffers, and to go on after", () => {
    const { video, audio, stalls } = pair();
    video.play();
    audio.dispatchEvent(new Event("waiting"));
    expect(stalls).toEqual([true]);
    audio.dispatchEvent(new Event("canplay"));
    expect(stalls).toEqual([true, false]);
  });

  it("detaches cleanly", () => {
    const { video, audio, f } = pair();
    video.play();
    f.detach();
    expect(audio.paused).toBe(true);
    video.currentTime = 5;
    video.dispatchEvent(new Event("seeking"));
    expect(audio.currentTime).not.toBe(5);
  });
});

describe("clip URLs", () => {
  it("names a recording <id>.m4a by default, with one .wav to try after", () => {
    expect(clipUrls("/runs/ed90/audio/", "c001")).toEqual(["/runs/ed90/audio/c001.m4a", "/runs/ed90/audio/c001.wav"]);
    expect(clipUrls("/runs/ed90/audio", "x002")).toEqual(["/runs/ed90/audio/x002.m4a", "/runs/ed90/audio/x002.wav"]);
  });
  it("takes the extension it is given, and a .wav extension has nothing after it", () => {
    expect(clipUrls("/a/", "c001", "mp3")).toEqual(["/a/c001.mp3", "/a/c001.wav"]);
    expect(clipUrls("/a/", "c001", ".WAV")).toEqual(["/a/c001.wav"]);
    expect(clipUrls("/a/", "c001", "wav")).toEqual(["/a/c001.wav"]);
    expect(clipUrls("/a/", "c001", "")).toEqual(["/a/c001.m4a", "/a/c001.wav"]);
  });
  it("fills a pattern as given", () => {
    expect(clipUrls("https://cdn.example/a/{id}.ogg", "c003")).toEqual(["https://cdn.example/a/c003.ogg"]);
  });
  it("refuses ids or extensions that could leave the folder, and no base", () => {
    expect(clipUrls("/audio/", "../secret")).toEqual([]);
    expect(clipUrls("/audio/", "")).toEqual([]);
    expect(clipUrls(null, "c001")).toEqual([]);
    expect(clipUrls("/audio/", "c001", "m4a/../x")).toEqual(["/audio/c001.m4a", "/audio/c001.wav"]);
  });
});

describe("extended holds", () => {
  it("wait for the NOTE pause, or the cue's span, never more than 30 s", () => {
    expect(MAX_HOLD_S).toBe(30);
    expect(holdFor({ start: 2.5, end: 4, pause: 1.5 })).toBe(1.5);
    expect(holdFor({ start: 1, end: 2, pause: 999 })).toBe(30);
    expect(holdFor({ start: 4, end: 104 })).toBe(30);
    expect(holdFor({ start: 4, end: 5.2 })).toBeCloseTo(1.2, 6);
    expect(holdFor({ start: 4, end: 4 })).toBe(0.5);
    expect(holdFor({ start: 4, end: 9, pause: Number.NaN })).toBe(5);
  });
});

describe("time words", () => {
  it("formats the readout and the slider's spoken value", () => {
    expect(clock(0)).toBe("0:00");
    expect(clock(83.9)).toBe("1:23");
    expect(clock(3725)).toBe("1:02:05");
    expect(spokenTime(0)).toBe("0 seconds");
    expect(spokenTime(1)).toBe("1 second");
    expect(spokenTime(65)).toBe("1 minute 5 seconds");
    expect(spokenTime(3600)).toBe("1 hour");
  });
});
