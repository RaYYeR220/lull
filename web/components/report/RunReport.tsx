import Link from "next/link";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { MetaList } from "@/components/ui/MetaList";
import { STATUS, StatusMark } from "@/components/ui/StatusMark";
import type { CueStatus } from "@/lib/api";
import { cx } from "@/lib/cx";
import type { ReportData } from "@/lib/report-data";
import { runHref, studioHref, watchHref } from "@/lib/routes";
import { modelName } from "@/lib/run-format";
import { formatTimecode } from "@/lib/timecode";
import styles from "./Report.module.css";

const ORDER: CueStatus[] = ["verified", "edited", "approved", "extended", "held"];
const pct = (r: number | null) => (r === null ? null : `${Math.round(r * 100)} %`);
const sentence = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function tone(status: string): "good" | "warn" | "none" {
  if (status.startsWith("met")) return "good";
  if (status === "needs review") return "warn";
  return "none";
}

/**
 * A run's conformance report: WCAG 1.2.3, 1.2.5 and 1.2.7 as the run's own data rates them,
 * with what blocks each; then coverage, timing, the lines, the models and the spend. Rows the
 * data doesn't carry are left out.
 */
export function RunReport({ r }: { r: ReportData }) {
  const watch = r.kind === "recorded" ? watchHref(r.id) : runHref(r.id);
  const lines = ORDER.filter((s) => (r.counts[s] ?? 0) > 0);
  return (
    <article className={styles.report} aria-labelledby="report-title">
      <header className={styles.head}>
        <Eyebrow className={styles.eyebrow}>{r.kind === "recorded" ? "Conformance report, recorded run" : "Conformance report, live run"}</Eyebrow>
        <h1 id="report-title" className={cx("t-display-2", styles.title)}>
          {r.title}
        </h1>
        {r.credit ? <p className={styles.credit}>{r.credit}</p> : null}
        <MetaList
          label="This run"
          className={styles.meta}
          items={[
            <span key="len">
              <b>{formatTimecode(r.duration, 0)}</b> long
            </span>,
            <Link key="watch" href={watch} className={styles.link}>
              Watch it described
            </Link>,
            <Link key="studio" href={studioHref(r.id)} className={styles.link} prefetch={false}>
              Open in Studio
            </Link>,
            ...(r.engineReport
              ? [
                  <a key="engine" href={r.engineReport} className={styles.link}>
                    The engine’s own report
                  </a>,
                ]
              : []),
          ]}
        />
      </header>

      {r.noSummary ? (
        <p className={styles.notice} data-no-summary="">
          This export doesn’t carry its run summary: it was made before the engine wrote one into exports. So its WCAG statuses, coverage and timing
          checks aren’t shown here; what follows comes from its published lines.
        </p>
      ) : null}

      {r.wcag ? (
        <section aria-labelledby="wcag-title" className={styles.section}>
          <h2 id="wcag-title" className={styles.h2}>
            WCAG audio description
          </h2>
          <table className={styles.table}>
            <thead>
              <tr>
                <th scope="col">Success criterion</th>
                <th scope="col">Status</th>
                <th scope="col">What blocks it, or what meets it</th>
              </tr>
            </thead>
            <tbody>
              {r.wcag.map((w) => (
                <tr key={w.criterion} data-criterion={w.criterion}>
                  <th scope="row">
                    <span className={styles.mono}>{w.criterion}</span> <span className={styles.level}>{w.level}</span>
                    <span className={styles.sub}>{w.name}</span>
                  </th>
                  <td className={styles.status} data-tone={tone(w.status)}>
                    {sentence(w.status)}
                  </td>
                  <td>
                    {w.blockers.length ? (
                      <ul className={styles.blockers}>
                        {w.blockers.map((b) => (
                          <li key={b}>{sentence(b)}</li>
                        ))}
                      </ul>
                    ) : w.via ? (
                      <span className={styles.via}>Met by {w.via}</span>
                    ) : (
                      <span className={styles.via}>Nothing blocks it.</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {r.reviewed !== null ? (
            <p className={styles.note} data-reviewed={String(r.reviewed)}>
              {r.reviewed
                ? "Every published line was approved or edited by a person."
                : "Not every published line has been reviewed by a person yet: a status reads “met” only once they all are."}
            </p>
          ) : null}
        </section>
      ) : null}

      {r.coverage || r.repetitive?.length ? (
        <section aria-labelledby="coverage-title" className={styles.section}>
          <h2 id="coverage-title" className={styles.h2}>
            Coverage
          </h2>
          <dl className={styles.facts}>
            {r.coverage ? (
              <>
                <div data-fact="coverage">
                  <dt>Important moments described</dt>
                  <dd>
                    {r.coverage.described ?? 0} of {r.coverage.important}
                    {pct(r.coverage.ratio) ? ` (${pct(r.coverage.ratio)})` : ""} in the standard track
                    {r.coverage.describedWithExtended !== null
                      ? `; ${r.coverage.describedWithExtended} of ${r.coverage.important}${pct(r.coverage.ratioWithExtended) ? ` (${pct(r.coverage.ratioWithExtended)})` : ""} with the extended track`
                      : ""}
                  </dd>
                </div>
                {r.coverage.undescribed.length ? (
                  <div>
                    <dt>Left undescribed</dt>
                    <dd className={styles.mono}>{r.coverage.undescribed.join(", ")}</dd>
                  </div>
                ) : null}
              </>
            ) : null}
            {r.repetitive?.length ? (
              <div data-fact="repetitive">
                <dt>Judged repetitive by the planner</dt>
                <dd>
                  <ul className={styles.plain}>
                    {r.repetitive.map((e) => (
                      <li key={e.id}>{e.text ?? `Event ${e.id}`}</li>
                    ))}
                  </ul>
                  <span className={styles.sub}>Not counted as missing.</span>
                </dd>
              </div>
            ) : null}
          </dl>
        </section>
      ) : null}

      {r.overlapSeconds !== null || r.f113 ? (
        <section aria-labelledby="timing-title" className={styles.section}>
          <h2 id="timing-title" className={styles.h2}>
            Timing
          </h2>
          <dl className={styles.facts}>
            {r.overlapSeconds !== null ? (
              <div data-fact="overlap">
                <dt>Over speech, a guard, protected sound or another line</dt>
                <dd>{r.overlapSeconds.toFixed(3)} s</dd>
              </div>
            ) : null}
            {r.f113 ? (
              <div data-fact="f113">
                <dt>Unused pauses of 2 s or more while something important went undescribed (F113)</dt>
                <dd>
                  {r.f113.length ? (
                    <ul className={styles.plain}>
                      {r.f113.map((f) => (
                        <li key={`${f.gapId}-${f.start}`}>
                          {formatTimecode(f.start)} to {formatTimecode(f.end)}
                          {f.events.length ? <span className={styles.mono}> ({f.events.join(", ")})</span> : null}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    "None"
                  )}
                </dd>
              </div>
            ) : null}
          </dl>
        </section>
      ) : null}

      <section aria-labelledby="lines-title" className={styles.section}>
        <h2 id="lines-title" className={styles.h2}>
          The lines
        </h2>
        <ul className={styles.counts} aria-label={r.countsFrom === "summary" ? "Lines by status" : "Published lines by status"}>
          {lines.map((s) => (
            <li key={s}>
              <span className={styles.count}>{r.counts[s]}</span> <StatusMark status={s} label={STATUS[s].word.toLowerCase()} />
            </li>
          ))}
        </ul>
        {r.countsFrom === "lines" ? <p className={styles.note}>Counted from the published lines.</p> : null}
        {r.held?.length ? (
          <dl className={styles.facts}>
            {r.held.map((h) => (
              <div key={h.id}>
                <dt>
                  Held <span className={styles.mono}>{h.id}</span>
                </dt>
                <dd>{h.reason ?? "No reason recorded."}</dd>
              </div>
            ))}
          </dl>
        ) : null}
        {r.ttsDisclosure ? <p className={styles.note}>{r.ttsDisclosure}</p> : null}
      </section>

      {r.models || r.spend ? (
        <section aria-labelledby="made-title" className={styles.section}>
          <h2 id="made-title" className={styles.h2}>
            What made it
          </h2>
          <dl className={styles.facts}>
            {r.models ? (
              <div data-fact="models">
                <dt>Models</dt>
                <dd>
                  <ul className={styles.plain}>
                    {r.models.map((m) => (
                      <li key={m}>
                        {modelName(m)} <span className={styles.mono}>{m}</span>
                      </li>
                    ))}
                  </ul>
                </dd>
              </div>
            ) : null}
            {r.spend?.map((s) => (
              <div key={s.name} data-fact="spend">
                <dt>{s.name}</dt>
                <dd>
                  {s.value}
                  {s.note ? <span className={styles.sub}>{s.note}</span> : null}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      ) : null}
    </article>
  );
}
