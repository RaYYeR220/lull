import type { Ratio } from "@/lib/benchmarks-schema";
import { cx } from "@/lib/cx";
import styles from "./Benchmarks.module.css";

export type IntervalRow = {
  id: string;
  name: string;
  /** What the share counts, and which way is better. */
  what: string;
  ratio: Ratio | null;
};

const TICKS = [0, 0.25, 0.5, 0.75, 1];
const pct = (x: number, digits = 1) => `${(x * 100).toFixed(digits)} %`;
const pos = (x: number) => `${(Math.min(1, Math.max(0, x)) * 100).toFixed(2)}%`;

/**
 * Shares with their 95 % intervals, one row each, on one axis from 0 to 100 %: a square
 * at the value and a whisker across the interval. It is a table first: every value, interval
 * and count is in the row as text, and the drawing beside it (hidden from assistive tech)
 * only shows the same numbers. One series, so no legend: the caption names it.
 */
export function IntervalChart({ caption, rows, className }: { caption: string; rows: IntervalRow[]; className?: string }) {
  return (
    <table className={cx(styles.chart, className)}>
      <caption className={styles.chartCaption}>{caption}</caption>
      <thead>
        <tr>
          <th scope="col">Measure</th>
          <th scope="col">Value</th>
          <th scope="col">95 % interval</th>
          <th scope="col">Count</th>
          <th scope="col" className={styles.axisHead}>
            <span className="sr-only">On a scale from 0 to 100 %</span>
            <span className={styles.axis} aria-hidden="true">
              {TICKS.map((t) => (
                <span key={t} style={{ left: pos(t) }}>
                  {Math.round(t * 100)}
                  {t === 1 ? " %" : ""}
                </span>
              ))}
            </span>
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => {
          const v = r.ratio?.value ?? null;
          const ci = r.ratio?.ci95 ?? null;
          return (
            <tr key={r.id} data-measure={r.id}>
              <th scope="row">
                <span className={styles.measureName}>{r.name}</span>
                <span className={styles.measureWhat}>{r.what}</span>
              </th>
              <td className={styles.num}>{v === null ? "None measured" : pct(v)}</td>
              <td className={styles.num}>{ci ? `${pct(ci[0])} to ${pct(ci[1])}` : "None"}</td>
              <td className={styles.num}>{r.ratio ? `${r.ratio.num} of ${r.ratio.den}` : "None"}</td>
              <td className={styles.plotCell} aria-hidden="true">
                <span className={styles.plot}>
                  {TICKS.map((t) => (
                    <i key={t} className={styles.grid} style={{ left: pos(t) }} />
                  ))}
                  {ci ? <span className={styles.whisker} style={{ left: pos(ci[0]), width: `calc(${pos(ci[1])} - ${pos(ci[0])})` }} /> : null}
                  {v !== null ? <span className={styles.dot} style={{ left: pos(v) }} /> : null}
                </span>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
