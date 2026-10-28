import { Section } from "@/components/ui/Section";
import Link from "next/link";
import { landingFindings, landingMetrics, type BenchmarkKey } from "@/lib/benchmarks-landing";
import type { Benchmarks as BenchmarkData } from "@/lib/benchmarks-schema";
import { cx } from "@/lib/cx";
import type { Showcase } from "@/lib/showcase";
import { spend } from "@/lib/showcase-derive";
import { spentUsd } from "@/lib/showcase-schema.mjs";
import styles from "./Benchmarks.module.css";

const METRICS: { key: BenchmarkKey; name: string; what: string }[] = [
  { key: "overlap", name: "Overlap with speech", what: "Seconds where a description plays over a word." },
  { key: "hallucination", name: "Hallucination rate", what: "Claims in a description that aren’t in the frames." },
  { key: "recall", name: "Visual-fact recall", what: "Visual facts a viewer needs that the description covers." },
  { key: "costPerMinute", name: "Cost per minute", what: "Model spend for each minute of video." },
];

/** 9. Benchmarks: the four measured numbers (empty cells until they are), what else the evaluation found, and the run's spend. */
export function Benchmarks({ benchmarks, showcase }: { benchmarks: BenchmarkData | null; showcase: Showcase }) {
  const cells = landingMetrics(benchmarks);
  const measured = METRICS.filter((m) => cells[m.key]).length;
  const findings = landingFindings(benchmarks);
  const spent = spentUsd(showcase);
  const cost = spent === undefined ? null : spend(spent);

  return (
    <Section
      id="benchmarks"
      eyebrow="Benchmarks"
      heading={measured === METRICS.length ? "Measured, misses included." : "Evaluation in progress."}
      lede={
        measured === 0
          ? "We’ll publish four numbers with the method and the test set beside them. Until they’re measured, these cells stay empty."
          : measured === METRICS.length
            ? "Four numbers from a pre-registered test on synthetic lectures, each with its interval. The comparison with human describers, the controls and every known failure are on the benchmarks page."
            : "Four numbers, each with the method and the test set beside it. Cells that aren’t measured yet stay empty."
      }
    >
      <div className={styles.grid}>
        <ul className={styles.metrics}>
          {METRICS.map((m) => {
            const got = cells[m.key];
            return (
              <li key={m.key} className={styles.metric}>
                <h3 className="t-title">{m.name}</h3>
                <p>{m.what}</p>
                {got ? (
                  <>
                    <span className={cx(styles.slot, styles.filled)}>
                      <span className={styles.value}>{got.value}</span>
                    </span>
                    {/* the denominator and interval, in view: a tooltip reaches no keyboard or touch */}
                    {got.detail ? <span className={styles.detail}>{got.detail}</span> : null}
                  </>
                ) : (
                  <span className={styles.slot}>Not yet measured</span>
                )}
              </li>
            );
          })}
        </ul>
        {findings.human || findings.check ? (
          <div className={styles.findings} data-findings="">
            {findings.human ? <p data-finding="human">{findings.human}</p> : null}
            {findings.check ? <p data-finding="check">{findings.check}</p> : null}
          </div>
        ) : null}
        <p className={styles.method}>
          <Link href="/benchmarks" className={styles.methodLink}>
            How it is measured
          </Link>
        </p>
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
