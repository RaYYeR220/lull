import type { WcagStatus } from "@/lib/api-runs";
import { cx } from "@/lib/cx";
import type { RunData } from "@/lib/run-data";
import styles from "./Studio.module.css";

const pct = (r: number) => `${Math.round(r * 100)} %`;

/** "met (automated checks)" → "Met (automated checks)". */
const sentence = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function tone(status: string): "good" | "warn" | "none" {
  if (status.startsWith("met")) return "good";
  if (status === "needs review") return "warn";
  return "none";
}

function Wcag({ rows }: { rows: WcagStatus[] }) {
  return (
    <ul className={styles.wcag} aria-label="WCAG statuses">
      {rows.map((w) => (
        <li key={w.criterion} data-tone={tone(w.status)}>
          <span className={styles.crit}>
            {w.criterion} <span className={styles.level}>{w.level}</span>
          </span>
          <span className={styles.wcagStatus}>{sentence(w.status)}</span>
          {w.reasons.length ? (
            <ul className={styles.reasons} aria-label={`What blocks ${w.criterion}`}>
              {w.reasons.map((r) => (
                <li key={r}>{sentence(r)}</li>
              ))}
            </ul>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

/**
 * The run at a glance, from its own summary: how many of the important moments are described,
 * what is held now, what the planner judged repetitive, and where each WCAG criterion stands
 * with what blocks it. Coverage and WCAG are worked out when the run is published; a figure the
 * run doesn't record is left out.
 */
export function RunSummary({ run }: { run: RunData }) {
  const s = run.summary;
  const held = run.cues.filter((c) => c.status === "held").length;
  if (!s) return null;
  const cov = s.coverage;
  const facts: { name: string; value: string; detail: string; tone?: "good" | "warn" }[] = [];
  if (cov && cov.important !== null && cov.described !== null) {
    const withExt = cov.describedWithExtended !== null && cov.describedWithExtended !== cov.described;
    facts.push({
      name: "Coverage",
      value: `${cov.described} of ${cov.important}`,
      detail:
        `Important moments described${cov.ratio !== null ? ` (${pct(cov.ratio)})` : ""}` +
        (withExt ? `; ${cov.describedWithExtended} with the extended lines.` : "."),
      tone: cov.described >= cov.important ? "good" : "warn",
    });
  }
  facts.push({
    name: "Held",
    value: String(held),
    detail: held ? `${held === 1 ? "A line waits" : "Lines wait"} for a person; held lines aren’t voiced.` : "No line is held.",
    tone: held ? "warn" : "good",
  });

  return (
    <section className={styles.summary} aria-labelledby="summary-title">
      <h2 id="summary-title" className="sr-only">
        The run at a glance
      </h2>
      <dl className={styles.facts}>
        {facts.map((f) => (
          <div key={f.name} className={styles.fact} data-tone={f.tone}>
            <dt>{f.name}</dt>
            <dd className={styles.factValue}>{f.value}</dd>
            <dd className={styles.factDetail}>{f.detail}</dd>
          </div>
        ))}
        {s.repetitive ? (
          <div className={styles.fact}>
            <dt>Judged repetitive</dt>
            <dd className={styles.factValue}>{s.repetitive.length}</dd>
            <dd className={styles.factDetail}>
              {s.repetitive.length ? (
                <details className={styles.more}>
                  <summary>Moments the planner rated as repeats, not counted as missing</summary>
                  <ul>
                    {s.repetitive.map((e) => (
                      <li key={e.id}>{e.text ?? `Event ${e.id}`}</li>
                    ))}
                  </ul>
                </details>
              ) : (
                "The planner judged no moment a repeat."
              )}
            </dd>
          </div>
        ) : null}
      </dl>
      {s.wcag?.length ? (
        <div className={cx(styles.fact, styles.wcagBlock)}>
          <p className={styles.wcagHead}>WCAG audio description</p>
          <Wcag rows={s.wcag} />
        </div>
      ) : null}
      {run.dirty ? <p className={styles.stale}>Coverage and WCAG are as of the last publish. Publish to bring your changes into them.</p> : null}
    </section>
  );
}
