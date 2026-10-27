import type { Ref } from "react";
import { Door, Gate } from "@/components/ui/Gate";
import { Glyph } from "@/components/ui/Glyph";
import { reportUrl, type CueStatus, type RunSummary } from "@/lib/api";
import { studioHref, watchHref } from "@/lib/routes";
import { clock, usd } from "@/lib/run-format";
import type { CueView } from "@/lib/run-state";
import shared from "./shared.module.css";
import styles from "./RunResult.module.css";

type Props = {
  runId: string;
  summary: RunSummary | null;
  /** Seconds from the run's first event to its last. */
  elapsed: number;
  /** Spend counted from the call events, used when the summary has none. */
  cost: number;
  cues: CueView[];
  onAgain: () => void;
  /** The heading, focused when the run finishes. */
  ref?: Ref<HTMLHeadingElement>;
};

function countsFrom(cues: CueView[]): Partial<Record<CueStatus, number>> {
  const out: Partial<Record<CueStatus, number>> = {};
  for (const c of cues) out[c.status] = (out[c.status] ?? 0) + 1;
  return out;
}

const sentence = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

/** The outcome in one line: what can be watched, and what waits for a person. */
function verdict(voiced: number, held: number): string {
  if (!voiced && !held) return "Finished with no lines to voice.";
  if (!voiced) return `Finished, with all ${held} ${held === 1 ? "line" : "lines"} held for review.`;
  if (!held) return "Ready to watch: every line passed its check.";
  return `Ready to watch, with ${held} ${held === 1 ? "line" : "lines"} held for review.`;
}

/** The finished run: what it produced, what it guarantees, and where to open it. */
export function RunResult({ runId, summary, elapsed, cost, cues, onAgain, ref }: Props) {
  const counts = summary?.cues && Object.keys(summary.cues).length ? summary.cues : countsFrom(cues);
  const voiced = (counts.verified ?? 0) + (counts.edited ?? 0) + (counts.approved ?? 0);
  const held = counts.held ?? 0;
  const extended = counts.extended ?? 0;
  const spend = summary?.costUsd ?? cost;
  const cov = summary?.coverage;
  const aa = summary?.wcag.find((w) => w.criterion === "1.2.5");

  const figures: { value: string; label: string }[] = [
    { value: String(voiced), label: voiced === 1 ? "line voiced in a pause" : "lines voiced in pauses" },
    { value: String(held), label: "held for review" },
  ];
  if (extended) figures.push({ value: String(extended), label: extended === 1 ? "extended line" : "extended lines" });
  if (summary?.overlapSeconds !== null && summary?.overlapSeconds !== undefined) {
    figures.push({ value: `${summary.overlapSeconds.toFixed(2)} s`, label: "of description over speech" });
  }
  if (cov && cov.ratio !== null) {
    figures.push({ value: `${Math.round(cov.ratio * 100)}%`, label: `of key moments described (${cov.described} of ${cov.salient})` });
  }
  if (summary?.costPerMin) figures.push({ value: usd(summary.costPerMin), label: "in model calls per minute of video" });

  return (
    <section aria-labelledby="result-title" className={styles.result}>
      <div className={styles.facts}>
        <h3 id="result-title" ref={ref} tabIndex={-1} className={styles.h}>
          {verdict(voiced, held)}
        </h3>
        <p className={styles.took}>
          Finished in {clock(elapsed)}, for {usd(spend)} in model calls.
        </p>
        <dl className={styles.figs}>
          {figures.map((f) => (
            <div key={f.label} className={styles.fig}>
              <dt>{f.label}</dt>
              <dd>{f.value}</dd>
            </div>
          ))}
        </dl>
        {aa ? (
          <p className={styles.wcag}>
            <span className={styles.crit}>
              WCAG {aa.criterion} ({aa.level})
            </span>{" "}
            {sentence(aa.status)}
            {aa.reasons.length ? `: ${aa.reasons.join("; ")}.` : "."}
          </p>
        ) : null}
      </div>

      <div className={styles.actions}>
        <Gate label="Open the result" className={styles.gate}>
          <Door tone="lit" href={watchHref(runId)} icon={<Glyph name="play" size={11} />}>
            Watch it described
          </Door>
          <Door tone="dark" href={studioHref(runId)}>
            Review in the studio
          </Door>
        </Gate>
        <div className={styles.more}>
          <a href={reportUrl(runId)} className={shared.link}>
            Read the report
          </a>
          <button type="button" className={shared.btn} onClick={onAgain}>
            Describe another clip
          </button>
        </div>
      </div>
    </section>
  );
}
