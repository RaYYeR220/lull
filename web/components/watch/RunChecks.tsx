import { REVIEWED, voicedLines, type RunData } from "@/lib/run-data";
import { plural, spend } from "@/lib/showcase-derive";
import styles from "./Watch.module.css";

type Check = { name: string; value: string; detail: string; tone?: "good" | "warn" };

/** Seconds of voiced standard lines over speech, worked out from the run's speech spans. */
function overSpeech(run: RunData): { cues: number; seconds: number } | null {
  // speech lent by the recorded run of the clip isn't this run's own measure
  if (!run.speech || run.borrowed?.speech) return null;
  let cues = 0;
  let seconds = 0;
  for (const c of voicedLines(run)) {
    if (c.end === null) continue;
    let mine = 0;
    for (const [a, b] of run.speech) mine += Math.max(0, Math.min(b, c.end) - Math.max(a, c.start));
    if (mine > 0) cues += 1;
    seconds += mine;
  }
  return { cues, seconds };
}

/**
 * What was checked on this run, read from the run itself: timing against speech and the
 * pauses, the frame check, the extended lines, review by a person, and the spend. A check the
 * run's data can't answer is left out.
 */
export function RunChecks({ run }: { run: RunData }) {
  const voiced = voicedLines(run);
  const all = run.cues;
  const extended = all.filter((c) => c.kind === "extended");
  const held = all.filter((c) => c.status === "held").length;
  const passed = all.length - held;
  const reviewed = all.filter((c) => REVIEWED.has(c.status)).length;
  const waits = extended.reduce((t, c) => t + (c.end !== null ? c.end - c.start : 0), 0);

  const checks: Check[] = [];
  // the API's own measure when it has one; otherwise the run's speech spans
  const measured = run.summary?.overlap?.speech ?? null;
  const computed = overSpeech(run);
  if (measured !== null || computed) {
    const seconds = measured ?? computed!.seconds;
    checks.push({
      name: "Over speech",
      value: `${seconds.toFixed(2)} s`,
      detail:
        seconds > 0
          ? computed && computed.cues
            ? `${plural(computed.cues, "line")} ${computed.cues === 1 ? "runs" : "run"} into someone speaking.`
            : "Some of the voiced lines run into someone speaking."
          : "No line is voiced over anyone speaking.",
      tone: seconds > 0 ? "warn" : "good",
    });
  }
  const placed = voiced.filter((c) => c.gap && c.end !== null);
  if (run.gaps && !run.borrowed?.gaps && voiced.length && placed.length === voiced.length) {
    const inside = placed.filter((c) => c.end! <= c.gap!.end + 1e-3).length;
    const used = new Set(placed.map((c) => c.gap!.id)).size;
    checks.push({
      name: "Inside its pause",
      value: `${inside} of ${voiced.length}`,
      detail:
        (inside === voiced.length
          ? "Each voiced line starts and ends inside the pause it was placed in"
          : `${plural(voiced.length - inside, "line")} ${voiced.length - inside === 1 ? "runs" : "run"} past the end of its pause`) +
        `; ${used} of ${run.gaps.length} pauses carry a line, the rest stay quiet.`,
      tone: inside === voiced.length ? "good" : "warn",
    });
  }
  checks.push({
    name: "Passed the frame check",
    value: `${passed} of ${all.length}`,
    detail: held
      ? `${plural(held, "line")} failed the check or ${held === 1 ? "was" : "were"} held for a person, and ${held === 1 ? "is" : "are"} not voiced.`
      : "Every line was checked claim by claim against the frames it describes before it was voiced.",
    tone: held ? "warn" : "good",
  });
  if (extended.length) {
    checks.push({
      name: "Extended",
      value: plural(extended.length, "line"),
      detail: `The video holds its picture for these, ${waits.toFixed(2)} s in all, in the extended version.`,
    });
  }
  checks.push({
    name: "Reviewed by a person",
    value: reviewed ? `${reviewed} of ${all.length}` : "Not yet",
    detail: reviewed
      ? "Lines a person approved or rewrote in the studio."
      : "The lines are checked by the engine and wait for a person’s review. They are voiced with synthetic speech.",
  });
  if (run.costUsd !== null) {
    const cost = spend(run.costUsd);
    checks.push({ name: "Model spend", value: cost.figure, detail: "For the full run: every model call made for it, not only its last pass." });
  }

  return (
    <section aria-labelledby="checks-title" className={styles.block}>
      <div className={styles.blockHead}>
        <h2 id="checks-title" className={styles.h2}>
          What was checked
        </h2>
      </div>
      <dl className={styles.checkList}>
        {checks.map((c) => (
          <div key={c.name} className={styles.check} data-tone={c.tone}>
            <dt>{c.name}</dt>
            <dd className={styles.checkValue}>{c.value}</dd>
            <dd className={styles.checkDetail}>{c.detail}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
