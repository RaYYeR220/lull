"use client";

import { memo, useEffect, useMemo, useRef, useState } from "react";
import { Glyph } from "@/components/ui/Glyph";
import { formatTimecode } from "@/lib/timecode";
import { PREVIEW_RATE, type Track } from "./horizon-controller";
import { amplitude, drawHorizon, type HorizonFrame, type HorizonModel } from "./horizon-draw";
import { useHorizonController, useHorizonState } from "./HorizonProvider";
import styles from "./Horizon.module.css";

export type HorizonData = {
  duration: number;
  title: string;
  loudness: number[];
  speech: [number, number][];
  gaps: { id: string; start: number; end: number; capacity: number }[];
  /** Voiced cues only, in time order, each with its pause. */
  cues: { id: string; start: number; end: number; text: string; gapId: string; gapStart: number; gapEnd: number }[];
  audio?: { original: string; described: string };
  /** Text alternative for the drawing. */
  label: string;
};

const pct = (t: number, T: number) => `${((t / T) * 100).toFixed(3)}%`;

/** Pause labels, cue lines and the timecode ruler. Static markup: the frame loop types into it. */
const Overlay = memo(function Overlay({ gaps, cues, duration: T }: Pick<HorizonData, "gaps" | "cues" | "duration">) {
  const ticks: number[] = [];
  for (let s = 0; s <= T; s += 10) ticks.push(s);
  return (
    <>
      {gaps.map((g) => (
        <div key={g.id} className={styles.gap} style={{ left: pct(g.start, T), width: pct(g.end - g.start, T) }}>
          <span className={styles.len}>{(g.end - g.start).toFixed(2)} s</span>
          <span className={styles.cap}>{g.capacity} words</span>
        </div>
      ))}
      {cues.map((c, i) => {
        // as wide as its pause (at least 150px), but always 14px short of the next line
        const next = cues[i + 1];
        const room = (((next ? next.gapStart : T) - c.gapStart) / T) * 100;
        const own = ((c.gapEnd - c.gapStart) / T) * 100;
        return (
          <p
            key={c.id}
            className={styles.cue}
            data-cue=""
            data-state="idle"
            style={{ left: pct(c.gapStart, T), width: `min(calc(${room.toFixed(3)}% - 14px), max(${own.toFixed(3)}%, 150px))` }}
          >
            <span />
            <span className={styles.caret} hidden />
            <span className={styles.rest}>{c.text}</span>
          </p>
        );
      })}
      <div className={styles.ticks}>
        {ticks.map((s) => (
          <span key={s} style={{ left: pct(s, T) }}>
            {formatTimecode(s, 0)}
          </span>
        ))}
      </div>
    </>
  );
});

/**
 * The hero's one object: 90 seconds of soundtrack drawn as a line on the floor, every pause
 * lit, and a playhead that speaks each description into its pause. Nothing plays until the
 * gate or Play is pressed; with real audio it plays at 1x, otherwise a silent 3x preview.
 */
export function Horizon({ data }: { data: HorizonData }) {
  const controller = useHorizonController();
  const { mode, playing, track } = useHorizonState();
  const [live, setLive] = useState("");
  const [still, setStill] = useState(false);

  const figRef = useRef<HTMLElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const dataRef = useRef<HTMLDivElement>(null);
  const readoutRef = useRef<HTMLSpanElement>(null);
  const originalRef = useRef<HTMLAudioElement>(null);
  const describedRef = useRef<HTMLAudioElement>(null);

  // the line's amplitude: real loudness inside speech, flat in the pauses
  const model = useMemo<HorizonModel>(() => {
    const inSpeech = (t: number) => data.speech.some(([a, b]) => t >= a - 0.05 && t <= b + 0.05);
    return {
      duration: data.duration,
      gaps: data.gaps,
      cues: data.cues.map((c) => ({ start: c.start, end: c.end, gapId: c.gapId })),
      amp: data.loudness.map((db, i) => (inSpeech(i / 10) ? amplitude(db) : 0)),
    };
  }, [data]);

  // canvas, frame loop and observers
  useEffect(() => {
    const stage = stageRef.current;
    const canvas = canvasRef.current;
    const scroller = scrollRef.current;
    const overlay = dataRef.current;
    const readout = readoutRef.current;
    const fig = figRef.current;
    const ctx = canvas?.getContext("2d");
    if (!stage || !canvas || !scroller || !overlay || !readout || !fig || !ctx) return;

    const T = data.duration;
    const cues = data.cues;
    const rm = window.matchMedia("(prefers-reduced-motion: reduce)");
    const lines = [...overlay.querySelectorAll<HTMLElement>("[data-cue]")].map((p) => ({
      p,
      typed: p.children[0] as HTMLElement,
      caret: p.children[1] as HTMLElement,
      rest: p.children[2] as HTMLElement,
    }));
    const shown = cues.map(() => -1);
    const frame: HorizonFrame = {
      width: 0,
      height: 0,
      dpr: 1,
      pad: 48,
      lineY: 166,
      enter: rm.matches ? 1 : 0,
      t: 0,
      started: false,
      ended: false,
      progress: cues.map(() => 0),
    };
    let visible = true;
    let raf = 0;
    let last = 0;

    const progressOf = (c: (typeof cues)[number], started: boolean) => {
      if (rm.matches && !started) return 1; // reduced motion rests on the final state
      if (!started) return 0;
      return Math.min(1, Math.max(0, (controller.t - c.start) / Math.max(1e-3, c.end - c.start)));
    };

    const updateLines = (started: boolean) => {
      let now = -1;
      cues.forEach((c, i) => {
        const line = lines[i];
        const p = frame.progress[i] ?? 0;
        const k = p >= 1 ? c.text.length : Math.floor(c.text.length * p);
        if (line && k !== shown[i]) {
          shown[i] = k;
          line.typed.textContent = c.text.slice(0, k);
          line.rest.textContent = c.text.slice(k);
          line.p.dataset.state = k === 0 ? "idle" : k < c.text.length ? "typing" : "done";
          line.caret.hidden = !(k > 0 && k < c.text.length);
        }
        if (started && controller.t >= c.start && controller.t < c.end) now = i;
      });
      if (now !== -1 && now !== controller.spoken) {
        controller.spoken = now;
        setLive(cues[now]?.text ?? "");
      }
    };

    const render = () => {
      const s = controller.getSnapshot();
      frame.t = controller.t;
      frame.started = s.started;
      frame.ended = s.ended;
      frame.progress = cues.map((c) => progressOf(c, s.started));
      drawHorizon(ctx, model, frame);
      updateLines(s.started);
      readout.textContent = `${formatTimecode(controller.t, 1)} / ${formatTimecode(T, 1)}`;
    };

    const size = () => {
      frame.dpr = Math.min(window.devicePixelRatio || 1, 2);
      frame.width = stage.clientWidth;
      frame.height = stage.clientHeight;
      frame.pad = overlay.offsetLeft; // the resolved --pad, so canvas and overlay share one axis
      frame.lineY = parseFloat(getComputedStyle(stage).getPropertyValue("--line-y")) || 166;
      canvas.width = Math.round(frame.width * frame.dpr);
      canvas.height = Math.round(frame.height * frame.dpr);
      render();
    };

    const loop = (now: number) => {
      raf = 0;
      const dt = last ? Math.min(0.1, (now - last) / 1000) : 0;
      last = now;
      const s = controller.getSnapshot();
      if (s.playing) {
        const audio = controller.activeAudio();
        controller.t = audio ? Math.min(T, Math.max(0, audio.currentTime)) : Math.min(T, controller.t + dt * PREVIEW_RATE);
        if (controller.t >= T) controller.finish();
        // on phones the band scrolls to keep the playhead in view
        if (visible && scroller.scrollWidth > scroller.clientWidth + 2) {
          const x = frame.pad + (controller.t / T) * (frame.width - 2 * frame.pad);
          const target = Math.max(0, x - scroller.clientWidth * 0.42);
          if (Math.abs(scroller.scrollLeft - target) > 1) scroller.scrollLeft = target;
        }
      }
      if (frame.enter < 1) frame.enter = Math.min(1, frame.enter + dt / 0.9);
      if (visible) render();
      if (raf) return; // a state change inside this frame already asked for the next one
      if (controller.getSnapshot().playing || frame.enter < 1) raf = requestAnimationFrame(loop);
      else last = 0;
    };
    const kick = () => {
      if (raf) return;
      last = 0;
      raf = requestAnimationFrame(loop);
    };
    const detachLoop = controller.attachLoop(kick);

    const resize = new ResizeObserver(size);
    resize.observe(stage);

    // off screen: stop drawing, and stop the silent preview (real sound keeps playing)
    const io = new IntersectionObserver(([entry]) => {
      visible = entry?.isIntersecting ?? true;
      const s = controller.getSnapshot();
      if (!visible && s.playing && s.mode === "visual") controller.pause();
      if (visible) kick();
    });
    io.observe(fig);

    const onMotion = () => {
      if (rm.matches) frame.enter = 1;
      render();
    };
    rm.addEventListener("change", onMotion);

    size();
    let alive = true;
    document.fonts?.ready.then(() => alive && size());
    // the entrance: the line draws in once, after the type settles
    const entrance = rm.matches ? 0 : window.setTimeout(kick, 380);

    return () => {
      alive = false;
      window.clearTimeout(entrance);
      if (raf) cancelAnimationFrame(raf);
      resize.disconnect();
      io.disconnect();
      rm.removeEventListener("change", onMotion);
      detachLoop();
    };
  }, [controller, data, model]);

  // real sound: switch over once both soundtracks have loaded their metadata
  useEffect(() => {
    const original = originalRef.current;
    const described = describedRef.current;
    if (!original || !described) return;
    let detach: (() => void) | null = null;
    const ready = () => {
      if (!detach && original.readyState >= 1 && described.readyState >= 1) detach = controller.attachAudio({ original, described });
    };
    const ended = (e: Event) => {
      if (e.target === controller.activeAudio()) controller.finish();
    };
    for (const a of [original, described]) {
      a.addEventListener("loadedmetadata", ready);
      a.addEventListener("ended", ended);
    }
    ready();
    return () => {
      for (const a of [original, described]) {
        a.removeEventListener("loadedmetadata", ready);
        a.removeEventListener("ended", ended);
      }
      detach?.();
    };
  }, [controller]);

  const toggleLamp = () => {
    const next = !still;
    document.body.classList.toggle("is-still", next);
    setStill(next);
  };

  const seconds = Math.round(data.duration);
  const trackButton = (k: Track, label: string) => (
    <button className={styles.btn} type="button" aria-pressed={track === k} onClick={() => controller.setTrack(k)}>
      {label}
    </button>
  );

  return (
    <figure className={styles.horizon} id="horizon" aria-labelledby="hz-caption" ref={figRef}>
      <div className={styles.scroll} ref={scrollRef} role="group" tabIndex={0} aria-label={`Timeline of the ${seconds}-second clip, scrolls sideways on small screens`}>
        <div className={styles.stage} ref={stageRef} data-horizon-stage="">
          <canvas ref={canvasRef} role="img" aria-label={data.label} />
          <div className={styles.data} ref={dataRef} aria-hidden="true">
            <Overlay gaps={data.gaps} cues={data.cues} duration={data.duration} />
          </div>
        </div>
      </div>
      <figcaption className={styles.bar}>
        <p className={styles.caption} id="hz-caption">
          The line traces who speaks in {seconds} seconds of <cite>{data.title}</cite>. Lull writes only into the lit pauses.
        </p>
        <div className={styles.transport} role="group" aria-label="Preview controls">
          {mode === "audio" ? (
            <span className={styles.ab} role="group" aria-label="Soundtrack">
              {trackButton("original", "Original")}
              {trackButton("described", "Described")}
            </span>
          ) : (
            <span className={styles.tag}>{PREVIEW_RATE}× preview</span>
          )}
          <span className={styles.readout} ref={readoutRef} aria-hidden="true" data-readout="">
            {`${formatTimecode(0, 1)} / ${formatTimecode(data.duration, 1)}`}
          </span>
          <button className={styles.btn} type="button" onClick={controller.toggle}>
            <Glyph name={playing ? "pause" : "play"} />
            <span>{playing ? "Pause" : "Play"}</span>
          </button>
          <button className={styles.btn} type="button" onClick={controller.replay}>
            <Glyph name="replay" />
            Replay
          </button>
          <button className={`${styles.btn} ${styles.lampToggle}`} type="button" aria-pressed={still} onClick={toggleLamp}>
            {still ? "Resume lamp" : "Pause lamp"}
          </button>
        </div>
      </figcaption>
      <p className="sr-only" aria-live="polite" data-horizon-live="">
        {live}
      </p>
      {data.audio ? (
        <>
          <audio ref={originalRef} preload="metadata" src={data.audio.original} />
          <audio ref={describedRef} preload="metadata" src={data.audio.described} />
        </>
      ) : null}
    </figure>
  );
}
