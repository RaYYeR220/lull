"use client";

import { memo, useState, type ReactNode } from "react";
import { StatusMark } from "@/components/ui/StatusMark";
import { Timecode } from "@/components/ui/Timecode";
import { useAfterLoad } from "@/components/ui/useAfterLoad";
import { cx } from "@/lib/cx";
import { fitWords, type RunCue } from "@/lib/run-data";
import { countWords } from "@/lib/studio-budget";
import styles from "./Watch.module.css";

/**
 * The run's evidence frame for a line, or an empty frame when there is none (or it doesn't load).
 * With `deferred`, the picture is fetched only once the page has loaded: a script's frames never
 * compete with the player for the first seconds' bandwidth.
 */
export function EvidenceFrame({
  src,
  className,
  empty,
  deferred = false,
}: {
  src: string | null;
  className?: string;
  /** Said in place of a missing frame. */
  empty?: ReactNode;
  deferred?: boolean;
}) {
  const [failed, setFailed] = useState<string | null>(null);
  const loaded = useAfterLoad();
  if (!src || failed === src || (deferred && !loaded)) return <span className={className}>{failed === src || !src ? empty : null}</span>;
  // eslint-disable-next-line @next/next/no-img-element -- the run's own 854x480 still, shown small
  return <img className={className} src={src} alt="" width={854} height={480} loading="lazy" decoding="async" onError={() => setFailed(src)} />;
}

const Row = memo(function Row({ cue, current, onSeek, borrowed }: { cue: RunCue; current: boolean; onSeek: (cue: RunCue) => void; borrowed: boolean }) {
  return (
    <li className={cx(styles.line, current && styles.current)} data-line={cue.id} data-status={cue.status}>
      <button type="button" className={styles.lineBtn} aria-current={current ? "true" : undefined} onClick={() => onSeek(cue)}>
        <EvidenceFrame src={cue.frame} className={styles.frame} deferred />
        <span className={styles.when}>
          <Timecode seconds={cue.start} className={styles.tc} />
          <span className={styles.label}>{cue.label}</span>
        </span>
        <span className={styles.said}>
          {cue.text}
          {cue.status === "held" ? (
            <span className={styles.reason}>
              Held for review{cue.note ? `: ${cue.note.replace(/\.$/, "")}` : ""}. Not voiced.
            </span>
          ) : null}
        </span>
        <span className={styles.verdict}>
          <StatusMark status={cue.status} />
          <span className={styles.fit}>{fitWords(cue, countWords, { borrowed })}</span>
        </span>
      </button>
    </li>
  );
});

/**
 * The description script: every line in time order with its frame, time, words and status; a
 * held line with the reason it was held.
 * Choosing a line moves the player there; the line being spoken is marked.
 */
export const CueScript = memo(function CueScript({
  cues,
  active,
  onSeek,
  className,
  borrowedGaps = false,
  heldElsewhere = 0,
}: {
  cues: RunCue[];
  active: string | null;
  onSeek: (cue: RunCue) => void;
  className?: string;
  /** The pauses are the recorded run's: each line's fit says so. */
  borrowedGaps?: boolean;
  /** Lines the run held for review that its files don't list (an export from before they were listed). */
  heldElsewhere?: number;
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
          <Row key={c.id} cue={c} current={c.id === active} onSeek={onSeek} borrowed={borrowedGaps} />
        ))}
      </ol>
      {heldElsewhere > 0 ? (
        <p className={styles.note} data-held-elsewhere="">
          {heldElsewhere === 1 ? "One more line was" : `${heldElsewhere} more lines were`} held for review and not voiced. This export doesn’t include{" "}
          {heldElsewhere === 1 ? "it" : "them"}, so {heldElsewhere === 1 ? "it isn’t" : "they aren’t"} listed here; the conformance report counts{" "}
          {heldElsewhere === 1 ? "it" : "them"}.
        </p>
      ) : null}
    </section>
  );
});
