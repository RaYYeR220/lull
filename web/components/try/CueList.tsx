import { memo } from "react";
import { StatusMark } from "@/components/ui/StatusMark";
import { Timecode } from "@/components/ui/Timecode";
import { cx } from "@/lib/cx";
import { secs } from "@/lib/run-format";
import type { CueView } from "@/lib/run-state";
import shared from "./shared.module.css";
import styles from "./CueList.module.css";

const CueRow = memo(function CueRow({ cue, latest }: { cue: CueView; latest: boolean }) {
  const held = cue.status === "held";
  return (
    <li className={cx(styles.row, held && styles.held, latest && styles.latest)} data-cue={cue.id} data-status={cue.status}>
      <span className={styles.n}>{cue.extended ? `Extended ${cue.n}` : `Cue ${cue.n}`}</span>
      <Timecode seconds={cue.start} className={styles.tc} />
      <p className={styles.text}>{cue.text}</p>
      <span className={styles.spoken}>
        {cue.audio_dur !== null && !held ? `${secs(cue.audio_dur, 1)} spoken` : held ? "Not voiced" : null}
      </span>
      <StatusMark status={cue.status} className={styles.s} />
      {cue.note ? (
        <p className={styles.note}>
          <span className="sr-only">{held ? "Why it was held: " : "What the check changed: "}</span>
          {cue.note}
        </p>
      ) : null}
    </li>
  );
});

/** Every line as it is finalised: its time, its words, and the verdict with the reason for a hold. */
export const CueList = memo(function CueList({ cues, latestId }: { cues: CueView[]; latestId: string | null }) {
  const held = cues.filter((c) => c.status === "held").length;
  const voiced = cues.length - held;
  return (
    <section aria-labelledby="cues-title" className={styles.wrap}>
      <div className={styles.head}>
        <h3 id="cues-title" className={shared.label}>
          Lines, in time order
        </h3>
        {cues.length ? (
          <p className={styles.count}>
            {voiced} {voiced === 1 ? "line" : "lines"} passed{held ? `, ${held} held` : ""}
          </p>
        ) : null}
      </div>
      {cues.length ? (
        <ol className={styles.list}>
          {cues.map((c) => (
            <CueRow key={c.id} cue={c} latest={c.id === latestId} />
          ))}
        </ol>
      ) : (
        <p className={styles.empty}>Lines appear here as each one is checked against the frames and voiced.</p>
      )}
    </section>
  );
});
