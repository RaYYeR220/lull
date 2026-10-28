"use client";

import { Fragment, memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent, type RefObject } from "react";
import type { PlayerElement } from "@/components/player/Player";
import { Glyph } from "@/components/ui/Glyph";
import { STATUS } from "@/components/ui/StatusMark";
import { cx } from "@/lib/cx";
import { borrowedWords, type RunCue, type RunData } from "@/lib/run-data";
import { block, declutter, fitPps, neighbour, rulerTicks, scrollToShow, stepPps, waveHeights, zoomScroll, MAX_PPS, MIN_PPS } from "@/lib/studio-geometry";
import { formatTimecode } from "@/lib/timecode";
import styles from "./Timeline.module.css";

/** The zoom the timeline opens at on a narrow screen, where the whole clip would be too small to read. */
const READABLE_PPS = 8;
/** Space before the clip's start and after its end, so a mark at 0:00 and the last label aren't cut. */
const LEAD = 14;
const TAIL = 28;
/** How often the playhead follows the player. */
const TICK_MS = 100;
/** The cue labels' type: about 7 px a character, and 6 px clear between labels. */
const LABEL_ROOM = { charWidth: 7, gap: 6 };

const LEGEND: { status: RunCue["status"]; word: string }[] = [
  { status: "verified", word: "Verified" },
  { status: "edited", word: "Edited" },
  { status: "approved", word: "Approved" },
  { status: "extended", word: "Extended" },
  { status: "held", word: "Held" },
];

/** Pixels between the soundtrack's strokes: closer and they run together into a block. */
const STROKE_PITCH = 2;

/**
 * The soundtrack: a stroke for each slice of time, as tall as its loudest 100 ms frame, brighter
 * where someone speaks. Frames are pooled when the clip is drawn smaller than a frame per stroke.
 */
const Waveform = memo(function Waveform({ loudness, speech, width }: { loudness: number[]; speech: [number, number][] | null; width: number }) {
  const strokes = Math.max(1, Math.min(loudness.length, Math.floor(width / STROKE_PITCH)));
  const [quiet, spoken] = useMemo(() => {
    let q = "";
    let s = "";
    const heights = waveHeights(loudness, 18);
    const per = loudness.length / strokes;
    for (let i = 0; i < strokes; i++) {
      const a0 = Math.floor(i * per);
      const a1 = Math.max(a0 + 1, Math.floor((i + 1) * per));
      let h = 0;
      for (let k = a0; k < a1 && k < heights.length; k++) h = Math.max(h, heights[k]!);
      const t0 = a0 / 10;
      const t1 = a1 / 10;
      const seg = `M${i + 0.5} ${(20 - h).toFixed(1)}V${(20 + h).toFixed(1)}`;
      if (speech?.some(([x, y]) => x < t1 + 0.05 && y > t0 - 0.05)) s += seg;
      else q += seg;
    }
    return [q, s];
  }, [loudness, speech, strokes]);
  return (
    <svg className={styles.wave} style={{ width }} viewBox={`0 0 ${strokes} 40`} preserveAspectRatio="none" aria-hidden="true" focusable="false">
      <path d={quiet} stroke="rgb(246 238 223 / .3)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
      <path d={spoken} stroke="rgb(246 238 223 / .88)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
    </svg>
  );
});

/** A line's name for assistive tech: its visible label first, then what it is. */
function optionName(c: RunCue): string {
  const when = formatTimecode(c.start);
  return `${c.label}, ${STATUS[c.status].word}, at ${when}: ${c.text}`;
}

type Props = {
  run: RunData;
  selected: string | null;
  onSelect: (id: string) => void;
  /** Enter on a line: open it in the drawer. */
  onOpen: () => void;
  player: RefObject<PlayerElement | null>;
  /** Lines the overlap gate named. */
  blocked?: ReadonlySet<string>;
};

/**
 * The run's timeline: the soundtrack, speech, every pause with what it holds, and each line by
 * status. The lines are one listbox (←/→ or J/K step through them in time order, Home/End jump,
 * Enter opens the selected one); +/− and 0 zoom; a press on the lanes moves the player there,
 * and the playhead follows the player.
 */
export function Timeline({ run, selected, onSelect, onOpen, player, blocked }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLDivElement>(null);
  const head = useRef<HTMLDivElement>(null);
  const clockText = useRef<HTMLSpanElement>(null);
  const listbox = useRef<HTMLDivElement>(null);
  const [pps, setPps] = useState(READABLE_PPS);
  const ppsRef = useRef(pps);
  const pendingScroll = useRef<number | null>(null);
  const focusSelected = useRef(false);
  const [fit, setFit] = useState<number>(READABLE_PPS);

  const T = Math.max(run.duration, 1);
  const order = useMemo(() => [...run.cues].sort((a, b) => a.start - b.start || a.id.localeCompare(b.id)), [run.cues]);
  const ids = useMemo(() => order.map((c) => c.id), [order]);
  const standard = useMemo(() => order.filter((c) => c.kind === "standard"), [order]);
  const extended = useMemo(() => order.filter((c) => c.kind === "extended"), [order]);
  const selectedCue = order.find((c) => c.id === selected) ?? null;

  /** Whether the person has zoomed: until then a new width brings back the opening zoom. */
  const zoomed = useRef(false);

  // open at the whole clip where it is readable, else at a readable zoom; again whenever the
  // view's width changes, unless the person has chosen a zoom of their own
  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    let width = -1;
    const measure = () => {
      if (el.clientWidth === width) return;
      width = el.clientWidth;
      const f = fitPps(width - LEAD - TAIL, T);
      setFit(f);
      if (!zoomed.current) setPps(Math.max(f, Math.min(READABLE_PPS, MAX_PPS)));
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [T]);

  useLayoutEffect(() => {
    ppsRef.current = pps;
    const el = scroller.current;
    if (el && pendingScroll.current !== null) {
      el.scrollLeft = pendingScroll.current;
      pendingScroll.current = null;
    }
    if (head.current) head.current.style.transform = `translateX(${((player.current?.currentTime ?? 0) * pps).toFixed(1)}px)`;
  }, [pps, player]);

  const zoomTo = useCallback(
    (next: number) => {
      const el = scroller.current;
      const from = ppsRef.current;
      if (!el || Math.abs(next - from) < 1e-6) return;
      // keep the selected line where it is when it is in view, else the middle of the view
      let anchor = el.clientWidth / 2;
      if (selectedCue) {
        const x = LEAD + selectedCue.start * from - el.scrollLeft;
        if (x >= 0 && x <= el.clientWidth) anchor = x;
      }
      // the clock's origin is LEAD pixels into the canvas
      pendingScroll.current = zoomScroll(el.scrollLeft, anchor - LEAD, from, next, el.clientWidth - LEAD - TAIL, T);
      // back at the whole clip, the zoom follows the view's width again
      zoomed.current = Math.abs(next - fit) > 1e-6;
      setPps(next);
    },
    [selectedCue, T, fit],
  );

  // the selected line stays in view; after a key press it also takes the focus
  useEffect(() => {
    const el = scroller.current;
    if (!el || !selectedCue) return;
    const b = block(selectedCue.start, selectedCue.end, pps, 14);
    const left = scrollToShow(LEAD + b.left, LEAD + b.left + b.width, el.scrollLeft, el.clientWidth);
    if (left !== el.scrollLeft) {
      const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      el.scrollTo({ left, behavior: still ? "auto" : "smooth" });
    }
    if (focusSelected.current) {
      focusSelected.current = false;
      listbox.current?.querySelector<HTMLElement>(`[data-cue="${CSS.escape(selectedCue.id)}"]`)?.focus({ preventScroll: true });
    }
  }, [selectedCue, pps]);

  // the playhead follows the player (written straight to the DOM: no re-render per tick)
  useEffect(() => {
    let last = -1;
    const id = window.setInterval(() => {
      if (document.hidden) return;
      const el = player.current;
      const t = el?.currentTime ?? 0;
      if (Math.abs(t - last) < 0.01) return;
      last = t;
      const x = t * ppsRef.current;
      if (head.current) head.current.style.transform = `translateX(${x.toFixed(1)}px)`;
      if (clockText.current) clockText.current.textContent = formatTimecode(t);
      const sc = scroller.current;
      // while it plays, keep the playhead in view
      const at = LEAD + x;
      if (sc && el?.state?.playing && (at < sc.scrollLeft || at > sc.scrollLeft + sc.clientWidth - 24)) sc.scrollLeft = Math.max(0, at - sc.clientWidth * 0.15);
    }, TICK_MS);
    return () => window.clearInterval(id);
  }, [player]);

  const seekTo = (e: MouseEvent<HTMLDivElement>) => {
    const c = canvas.current;
    const el = player.current;
    if (!c || !el?.seek) return;
    const x = e.clientX - c.getBoundingClientRect().left;
    el.seek(Math.min(T, Math.max(0, x / ppsRef.current)));
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    const key = e.key;
    let next: string | null = null;
    if (key === "ArrowRight" || key === "j" || key === "J") next = neighbour(ids, selected, 1);
    else if (key === "ArrowLeft" || key === "k" || key === "K") next = neighbour(ids, selected, -1);
    else if (key === "Home") next = neighbour(ids, selected, "first");
    else if (key === "End") next = neighbour(ids, selected, "last");
    else if (key === "+" || key === "=") zoomTo(stepPps(ppsRef.current, 1));
    else if (key === "-" || key === "_") zoomTo(stepPps(ppsRef.current, -1));
    else if (key === "0") zoomTo(fit);
    else if (key === "Enter") onOpen();
    else return;
    e.preventDefault();
    if (next && next !== selected) {
      focusSelected.current = true;
      onSelect(next);
    }
  };

  const ticks = rulerTicks(T, pps);
  // a short name under each line, where it doesn't run into the one before
  const named = useMemo(
    () =>
      new Set([
        ...declutter(
          standard.map((c) => ({ id: c.id, left: c.start * pps, label: String(c.n) })),
          LABEL_ROOM,
        ),
        ...declutter(
          extended.map((c) => ({ id: c.id, left: c.start * pps, label: `E${c.n}`, centred: true })),
          LABEL_ROOM,
        ),
      ]),
    [standard, extended, pps],
  );
  const width = LEAD + T * pps + TAIL;
  const option = (c: RunCue) => {
    const b = block(c.start, c.end, pps, c.kind === "extended" ? 18 : 14);
    const sel = c.id === selected;
    const mark = c.kind === "extended";
    return (
      <Fragment key={c.id}>
        <div
          id={`tl-${c.id}`}
          role="option"
          aria-selected={sel}
          aria-label={optionName(c)}
          tabIndex={sel || (!selected && c.id === ids[0]) ? 0 : -1}
          data-cue={c.id}
          data-status={c.status}
          data-blocked={blocked?.has(c.id) ? "" : undefined}
          className={cx(mark ? styles.mark : styles.cue, styles[c.status], sel && styles.sel)}
          style={mark ? { left: b.left } : { left: b.left, width: b.width }}
          onClick={() => onSelect(c.id)}
        >
          <Glyph name={STATUS[c.status].glyph} size={10} />
        </div>
        {/* the short name under the block; the option's own name says it in full */}
        {named.has(c.id) || sel ? (
          <span className={cx(styles.cueName, mark && styles.markName, sel && styles.nameOn)} style={{ left: b.left }} aria-hidden="true">
            {mark ? `E${c.n}` : c.n}
          </span>
        ) : null}
      </Fragment>
    );
  };

  const lanes: { key: string; label: string }[] = [
    ...(run.loudness ? [{ key: "wave", label: "Soundtrack" }] : []),
    ...(run.speech ? [{ key: "speech", label: "Speech" }] : []),
    ...(run.gaps ? [{ key: "gaps", label: "Pauses" }] : []),
    { key: "cues", label: "Lines" },
    ...(extended.length ? [{ key: "ext", label: "Extended" }] : []),
  ];
  const zoom = pps / fit;

  return (
    <section className={styles.timeline} aria-labelledby="tl-title">
      <div className={styles.tools}>
        <h2 id="tl-title" className={styles.title}>
          Timeline
        </h2>
        <span className={styles.clock} aria-hidden="true">
          <span ref={clockText}>{formatTimecode(0)}</span> / {formatTimecode(T)}
        </span>
        <div className={styles.zoom} role="group" aria-label="Zoom">
          <button type="button" className={styles.tool} aria-label="Zoom out" aria-keyshortcuts="-" onClick={() => zoomTo(stepPps(pps, -1))} disabled={pps <= Math.min(fit, MIN_PPS) + 1e-6}>
            <span aria-hidden="true">−</span>
          </button>
          <span className={styles.scale}>{zoom < 1.05 && zoom > 0.95 ? "1×" : `${zoom.toFixed(1)}×`}</span>
          <button type="button" className={styles.tool} aria-label="Zoom in" aria-keyshortcuts="+" onClick={() => zoomTo(stepPps(pps, 1))} disabled={pps >= MAX_PPS - 1e-6}>
            <span aria-hidden="true">+</span>
          </button>
          <button type="button" className={cx(styles.tool, styles.fit)} aria-keyshortcuts="0" onClick={() => zoomTo(fit)} disabled={Math.abs(pps - fit) < 1e-6}>
            Whole clip
          </button>
        </div>
      </div>
      <p id="tl-keys" className={styles.keys}>
        Step through the lines with the arrow keys or J and K, zoom with + and −, and press Enter to open the chosen line.
      </p>

      <div className={styles.frame}>
        <div className={styles.labels} aria-hidden="true">
          <span className={styles.rulerLabel} />
          {lanes.map((l) => (
            <span key={l.key} className={cx(styles.label, styles[`h_${l.key}`])}>
              {l.label}
            </span>
          ))}
        </div>
        <div className={styles.scroller} ref={scroller}>
          <div className={styles.canvas} style={{ width }}>
            <div className={styles.inner} ref={canvas}>
              <div className={styles.ruler} aria-hidden="true" onClick={seekTo}>
                {ticks.ticks.map((t) => (
                  <span key={t} className={styles.tick} style={{ left: t * pps }}>
                    <span>{formatTimecode(t, 0)}</span>
                  </span>
                ))}
              </div>
              <div className={styles.lanes} aria-hidden="true" onClick={seekTo}>
                {run.loudness ? (
                  <div className={cx(styles.lane, styles.h_wave)}>
                    <Waveform loudness={run.loudness} speech={run.speech} width={(run.loudness.length / 10) * pps} />
                  </div>
                ) : null}
                {run.speech ? (
                  <div className={cx(styles.lane, styles.h_speech)}>
                    {run.speech.map(([a, b]) => {
                      const w = (b - a) * pps;
                      const said =
                        w >= 70 && run.words
                          ? run.words
                              .filter((x) => x.start >= a - 0.01 && x.end <= b + 0.01)
                              .map((x) => x.text)
                              .join(" ")
                          : "";
                      return (
                        <span key={a} className={styles.spk} style={{ left: a * pps, width: w }}>
                          {said}
                        </span>
                      );
                    })}
                  </div>
                ) : null}
                {run.gaps ? (
                  <div className={cx(styles.lane, styles.h_gaps)}>
                    {run.gaps.map((g) => {
                      const w = (g.end - g.start) * pps;
                      return (
                        <span key={g.id} className={cx(styles.gap, selectedCue?.gap?.id === g.id && styles.gapOn)} style={{ left: g.start * pps, width: w }}>
                          {w >= 46 ? (
                            <>
                              <span className={styles.len}>{(g.end - g.start).toFixed(2)} s</span>
                              <span className={styles.room}>{g.capacity} words</span>
                            </>
                          ) : null}
                        </span>
                      );
                    })}
                  </div>
                ) : null}
              </div>
              <div ref={listbox} role="listbox" aria-label="Lines, in time order" aria-orientation="horizontal" aria-describedby="tl-keys" className={styles.list} onKeyDown={onKeyDown}>
                <div className={cx(styles.lane, styles.h_cues)} role="presentation">
                  {standard.map(option)}
                </div>
                {extended.length ? (
                  <div className={cx(styles.lane, styles.h_ext)} role="presentation">
                    {extended.map(option)}
                  </div>
                ) : null}
              </div>
              <div ref={head} className={styles.playhead} aria-hidden="true" />
            </div>
          </div>
        </div>
      </div>

      <ul className={styles.legend} aria-label="What the colours mean">
        {LEGEND.filter((l) => run.cues.some((c) => c.status === l.status)).map((l) => (
          <li key={l.status}>
            <i className={cx(styles.swatch, styles[l.status])} />
            {l.word}
          </li>
        ))}
        {run.gaps ? (
          <li>
            <i className={cx(styles.swatch, styles.swatchGap)} />
            Pause, with the words it holds
          </li>
        ) : null}
      </ul>
      {run.borrowed ? <p className={styles.borrowed}>{borrowedWords(run.borrowed)}</p> : null}
    </section>
  );
}
