import { Section } from "@/components/ui/Section";
import type { BenchmarkKey, Benchmarks as BenchmarkData } from "@/lib/benchmarks";
import { cx } from "@/lib/cx";
import type { Showcase } from "@/lib/showcase";
import { spend } from "@/lib/showcase-derive";
import styles from "./Benchmarks.module.css";

const METRICS: { key: BenchmarkKey; name: string; what: string }[] = [
  { key: "overlap", name: "Overlap with speech", what: "Seconds where a description plays over a word." },
  { key: "hallucination", name: "Hallucination rate", what: "Claims in a description that aren’t in the frames." },
  { key: "recall", name: "Visual-fact recall", what: "Visual facts a viewer needs that the description covers." },
  { key: "costPerMinute", name: "Cost per minute", what: "Model spend for each minute of video." },
];

/** 9. Benchmarks: four empty cells until they are measured, and the one number that has been. */
export function Benchmarks({ benchmarks, showcase }: { benchmarks: BenchmarkData | null; showcase: Showcase }) {
  const measured = METRICS.filter((m) => benchmarks?.metrics[m.key]).length;
  const cost = showcase.costUsd === undefined ? null : spend(showcase.costUsd);

  return (
    <Section
      id="benchmarks"
      eyebrow="Benchmarks"
      heading="Evaluation in progress."
      lede={
        measured === 0
          ? "We’ll publish four numbers with the method and the test set beside them. Until they’re measured, these cells stay empty."
          : "Four numbers, each with the method and the test set beside it. Cells that aren’t measured yet stay empty."
      }
    >
      <div className={styles.grid}>
        <ul className={styles.metrics}>
          {METRICS.map((m) => {
            const got = benchmarks?.metrics[m.key];
            return (
              <li key={m.key} className={styles.metric}>
                <h3 className="t-title">{m.name}</h3>
                <p>{m.what}</p>
                {got ? (
                  <span className={cx(styles.slot, styles.filled)} title={got.detail}>
                    <span className={styles.value}>{got.value}</span>
                  </span>
                ) : (
                  <span className={styles.slot}>Not yet measured</span>
                )}
              </li>
            );
          })}
        </ul>
        {cost ? (
          <aside className={styles.measured} aria-label="One measured number">
            <span className="t-numeral-xl">{cost.figure}</span>
            <p>
              {cost.words} in model calls for the full {Math.round(showcase.duration)}-second run of <cite>{showcase.title}</cite>.
            </p>
            <p className={styles.after}>
              It counts every model call made for the run, not only its last pass.
              {measured === 0 ? " The only figure here that has been measured so far." : null}
            </p>
          </aside>
        ) : null}
      </div>
    </Section>
  );
}
