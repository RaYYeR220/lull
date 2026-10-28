import type { Showcase } from "@/lib/showcase";
import { linesInsidePauses, numberedCues, plural, REVIEWED, spend, speechOverlap, voicedCues } from "@/lib/showcase-derive";
import styles from "./Watch.module.css";

type Check = { name: string; value: string; detail: string; tone?: "good" | "warn" };

/**
 * What was checked on this run, read from the run itself: timing against speech and the
 * pauses, the frame check, the extended lines, review by a person, and the spend.
 */
export function RunChecks({ showcase: s }: { showcase: Showcase }) {
  const voiced = voicedCues(s);
  const all = numberedCues(s);
  const extended = all.filter((c) => c.kind === "extended");
  const held = all.filter((c) => c.status === "held").length;
  const passed = all.length - held;
  const overlap = speechOverlap(s);
  const inside = linesInsidePauses(s);
  const used = new Set(voiced.map((c) => c.gap.id)).size;
  const reviewed = all.filter((c) => REVIEWED.has(c.status)).length;
  const waits = extended.reduce((t, c) => t + (c.end - c.start), 0);

  const checks: Check[] = [
    {
      name: "Over speech",
      value: `${overlap.seconds.toFixed(2)} s`,
      detail: overlap.cues ? `${plural(overlap.cues, "line")} runs into someone speaking.` : "No line is voiced over anyone speaking.",
      tone: overlap.seconds > 0 ? "warn" : "good",
    },
    {
      name: "Inside its pause",
      value: `${inside.inside} of ${inside.of}`,
      detail:
        (inside.inside === inside.of
          ? "Each voiced line starts and ends inside the pause it was placed in"
          : `${plural(inside.of - inside.inside, "line")} ${inside.of - inside.inside === 1 ? "runs" : "run"} past the end of its pause`) +
        `; ${used} of ${s.gaps.length} pauses carry a line, the rest stay quiet.`,
      tone: inside.inside === inside.of ? "good" : "warn",
    },
    {
      name: "Against the frames",
      value: `${passed} of ${all.length}`,
      detail: held
        ? `${plural(held, "line")} failed the check and ${held === 1 ? "is" : "are"} held for a person, not voiced.`
        : "Every line was checked claim by claim against the frames it describes before it was voiced.",
      tone: held ? "warn" : "good",
    },
  ];
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
  if (s.costUsd !== undefined) {
    const cost = spend(s.costUsd);
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
