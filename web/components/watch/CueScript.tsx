"use client";

import { memo } from "react";
import { StatusMark } from "@/components/ui/StatusMark";
import { Timecode } from "@/components/ui/Timecode";
import { cx } from "@/lib/cx";
import type { CueStatus } from "@/lib/showcase";
import styles from "./Watch.module.css";

/** One line of the script, as the page shows it. */
export type ScriptCue = {
  id: string;
  start: number;
  text: string;
  status: CueStatus;
  /** "Cue 3" or "Extended 1". */
  label: string;
  /** How it fits: "11 of 13 words", or how long the video waits for an extended line. */
  fit: string;
  /** The evidence frame behind the line. */
  frame: string | null;
};

const Row = memo(function Row({ cue, current, onSeek }: { cue: ScriptCue; current: boolean; onSeek: (cue: ScriptCue) => void }) {
  return (
    <li className={cx(styles.line, current && styles.current)} data-line={cue.id} data-status={cue.status}>
      <button type="button" className={styles.lineBtn} aria-current={current ? "true" : undefined} onClick={() => onSeek(cue)}>
        {cue.frame ? (
          // eslint-disable-next-line @next/next/no-img-element -- the run's own 854x480 still, shown small
          <img className={styles.frame} src={cue.frame} alt="" width={854} height={480} loading="lazy" decoding="async" />
        ) : (
          <span className={styles.frame} />
        )}
        <span className={styles.when}>
          <Timecode seconds={cue.start} className={styles.tc} />
          <span className={styles.label}>{cue.label}</span>
        </span>
        <span className={styles.said}>{cue.text}</span>
        <span className={styles.verdict}>
          <StatusMark status={cue.status} />
          <span className={styles.fit}>{cue.fit}</span>
        </span>
      </button>
    </li>
  );
});

/**
 * The description script: every line in time order with its frame, time, words and status.
 * Choosing a line moves the player there; the line being spoken is marked.
 */
export const CueScript = memo(function CueScript({
  cues,
  active,
  onSeek,
  className,
}: {
  cues: ScriptCue[];
  active: string | null;
  onSeek: (cue: ScriptCue) => void;
  className?: string;
}) {
  return (
    <section aria-labelledby="script-title" aria-describedby="script-how" className={cx(styles.script, className)}>
      <div className={styles.blockHead}>
        <h2 id="script-title" className={styles.h2}>
          The description script
        </h2>
        <p id="script-how" className={styles.note}>
          Every line in time order, with the frame it was checked against. Choose one to move the player there.
        </p>
      </div>
      <ol className={styles.lines}>
        {cues.map((c) => (
          <Row key={c.id} cue={c} current={c.id === active} onSeek={onSeek} />
        ))}
      </ol>
    </section>
  );
});
