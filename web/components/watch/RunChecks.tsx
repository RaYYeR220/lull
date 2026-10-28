import { runLines, voicedLines, type RunData } from "@/lib/run-data";
import { plural, spend } from "@/lib/showcase-derive";
import styles from "./Watch.module.css";

type Check = { name: string; value: string; detail: string; tone?: "good" | "warn" };

const sentence = (t: string) => t.charAt(0).toUpperCase() + t.slice(1);

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
  const extended = run.cues.filter((c) => c.kind === "extended" && c.status !== "held");
  const lines = runLines(run);
  const wcag = run.recorded?.wcag ?? run.summary?.wcag ?? null;
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
  const { passed, held, heldListed, reviewed } = lines;
  checks.push({
    name: "Passed the frame check",
    value: held === null ? plural(passed, "line") : `${passed} of ${passed + held}`,
    detail:
      held === null
        ? "Each voiced line was checked claim by claim against its frames. This export doesn’t record whether any line was held."
        : held
          ? `${held} held for review and not voiced` +
            (heldListed >= held ? "; each is in the script with the reason." : heldListed ? "; the script lists those this export carries." : "; this export doesn’t list them.")
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
    value: reviewed ? `${reviewed} of ${passed + lines.extended}` : "Not yet",
    detail: reviewed
      ? "Lines a person approved or rewrote in the studio."
      : "No line has been approved or edited by a person yet. The lines are checked by the engine and voiced with synthetic speech.",
  });
  if (wcag?.length) {
    const review = wcag.some((w) => w.status === "needs review");
    const met = wcag.every((w) => w.status === "met");
    const list = wcag.map((w) => w.criterion);
    const named = list.length > 1 ? `${list.slice(0, -1).join(", ")} and ${list[list.length - 1]}` : list[0];
    checks.push({
      name: "WCAG audio description",
      value: review ? "Needs review" : met ? "Met" : sentence(wcag[0]!.status),
      detail: review
        ? `${named} ${list.length > 1 ? "need" : "needs"} a person’s review first; the conformance report lists what blocks each.`
        : met
          ? `${named}: met, with every line reviewed by a person.`
          : `${named}: see the conformance report for each status.`,
      tone: review ? "warn" : met ? "good" : undefined,
    });
  }
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
