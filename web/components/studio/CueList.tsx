"use client";

import { memo } from "react";
import { StatusMark } from "@/components/ui/StatusMark";
import { Timecode } from "@/components/ui/Timecode";
import { cx } from "@/lib/cx";
import { fitWords, type RunCue } from "@/lib/run-data";
import { countWords } from "@/lib/studio-budget";
import styles from "./Studio.module.css";

const Row = memo(function Row({ cue, current, onChoose, borrowed }: { cue: RunCue; current: boolean; onChoose: (id: string) => void; borrowed: boolean }) {
  return (
    <li data-row={cue.id} data-status={cue.status} className={cx(styles.row, current && styles.rowOn)}>
      <button type="button" className={styles.rowBtn} aria-current={current ? "true" : undefined} onClick={() => onChoose(cue.id)}>
        <span className={styles.rowName}>{cue.label}</span>
        <Timecode seconds={cue.start} className={styles.rowTime} />
        <span className={styles.rowText}>{cue.text}</span>
        <span className={styles.rowFit}>{fitWords(cue, countWords, { borrowed })}</span>
        <StatusMark status={cue.status} className={styles.rowStatus} />
      </button>
    </li>
  );
});

/** Every line in time order, to read through and choose from; the chosen one opens in the drawer. */
export const CueList = memo(function CueList({
  cues,
  selected,
  onChoose,
  borrowedGaps = false,
}: {
  cues: RunCue[];
  selected: string | null;
  onChoose: (id: string) => void;
  /** The pauses are the recorded run's: each line's fit says so. */
  borrowedGaps?: boolean;
}) {
  const held = cues.filter((c) => c.status === "held").length;
  return (
    <section className={styles.listBlock} aria-labelledby="lines-title">
      <div className={styles.listHead}>
        <h2 id="lines-title" className={styles.h2}>
          Every line
        </h2>
        <p className={styles.listCount}>
          {cues.length} {cues.length === 1 ? "line" : "lines"}
          {held ? `, ${held} held` : ""}
        </p>
      </div>
      <ol className={styles.rows}>
        {cues.map((c) => (
          <Row key={c.id} cue={c} current={c.id === selected} onChoose={onChoose} borrowed={borrowedGaps} />
        ))}
      </ol>
    </section>
  );
});
