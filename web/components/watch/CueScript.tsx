"use client";

import { memo, useState } from "react";
import { StatusMark } from "@/components/ui/StatusMark";
import { Timecode } from "@/components/ui/Timecode";
import { cx } from "@/lib/cx";
import { fitWords, type RunCue } from "@/lib/run-data";
import { countWords } from "@/lib/studio-budget";
import styles from "./Watch.module.css";

/** The run's evidence frame for a line, or an empty frame when there is none (or it doesn't load). */
export function EvidenceFrame({ src, className }: { src: string | null; className?: string }) {
  const [failed, setFailed] = useState<string | null>(null);
  if (!src || failed === src) return <span className={className} />;
  // eslint-disable-next-line @next/next/no-img-element -- the run's own 854x480 still, shown small
  return <img className={className} src={src} alt="" width={854} height={480} loading="lazy" decoding="async" onError={() => setFailed(src)} />;
}

const Row = memo(function Row({ cue, current, onSeek }: { cue: RunCue; current: boolean; onSeek: (cue: RunCue) => void }) {
  return (
    <li className={cx(styles.line, current && styles.current)} data-line={cue.id} data-status={cue.status}>
      <button type="button" className={styles.lineBtn} aria-current={current ? "true" : undefined} onClick={() => onSeek(cue)}>
        <EvidenceFrame src={cue.frame} className={styles.frame} />
        <span className={styles.when}>
          <Timecode seconds={cue.start} className={styles.tc} />
          <span className={styles.label}>{cue.label}</span>
        </span>
        <span className={styles.said}>{cue.text}</span>
        <span className={styles.verdict}>
          <StatusMark status={cue.status} />
          <span className={styles.fit}>{fitWords(cue, countWords)}</span>
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
  cues: RunCue[];
  active: string | null;
  onSeek: (cue: RunCue) => void;
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
