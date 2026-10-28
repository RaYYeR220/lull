import { cx } from "@/lib/cx";
import { budgetWords, type Budget } from "@/lib/studio-budget";
import styles from "./BudgetMeter.module.css";

/** Past this many words a pause is drawn as one bar, not a slot per word. */
const MAX_SLOTS = 32;
/** Words drawn past the pause's edge at most (the count says the rest). */
const MAX_SPILL = 12;

/**
 * A line's words against its pause, drawn as the pause itself: one slot per word it holds,
 * lit as the line fills them, and the words that don't fit spilling past its edge, hatched.
 * The words and seconds are said in text beside it (`id`, for the editor's description).
 */
export function BudgetMeter({ budget: b, id, className }: { budget: Budget; id?: string; className?: string }) {
  const seconds =
    b.seconds !== null
      ? b.room !== null
        ? `about ${b.seconds.toFixed(1)} s to say, in a ${b.room.toFixed(2)} s pause`
        : `about ${b.seconds.toFixed(1)} s to say; the video waits for it`
      : b.room !== null
        ? `in a ${b.room.toFixed(2)} s pause`
        : null;

  let drawing = null;
  if (b.capacity !== null) {
    const filled = Math.min(b.words, b.capacity);
    const spill = Math.max(0, b.words - b.capacity);
    if (b.capacity <= MAX_SLOTS) {
      drawing = (
        <span className={styles.slots} aria-hidden="true">
          <span className={styles.pause}>
            {Array.from({ length: b.capacity }, (_, i) => (
              <i key={i} className={i < filled ? styles.on : undefined} />
            ))}
          </span>
          {spill ? (
            <span className={styles.spill}>
              {Array.from({ length: Math.min(spill, MAX_SPILL) }, (_, i) => (
                <i key={i} />
              ))}
            </span>
          ) : null}
        </span>
      );
    } else {
      drawing = (
        <span className={styles.bar} aria-hidden="true">
          <span className={styles.barPause}>
            <i style={{ width: `${(Math.min(1, b.fill ?? 0) * 100).toFixed(1)}%` }} />
          </span>
          {spill ? <span className={styles.barSpill} style={{ width: `${Math.min(40, (spill / b.capacity) * 100).toFixed(1)}%` }} /> : null}
        </span>
      );
    }
  }

  return (
    <div className={cx(styles.meter, b.over && styles.over, className)} data-over={b.over ? "" : undefined}>
      {drawing}
      <p id={id} className={styles.words}>
        <span className={styles.count}>{budgetWords(b)}</span>
        {seconds ? <span className={styles.secs}>{seconds}</span> : null}
      </p>
    </div>
  );
}
