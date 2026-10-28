import type { Agreement, OverlapSide, Ratio, SuiteB, SuiteBRecall, SuiteBReference } from "@/lib/benchmarks-schema";
import { humanCeiling, referenceName, sideName } from "@/lib/benchmarks-text";
import { modelName } from "@/lib/run-format";
import styles from "./Benchmarks.module.css";

const pct = (x: number | null) => (x === null ? "None" : `${(x * 100).toFixed(1)} %`);
const short = (sha: string) => sha.slice(0, 12);
const day = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
};

/** A share as the page writes it everywhere: value, its 95 % interval, and the count. */
export function Share({ r }: { r: Ratio | null }) {
  if (!r || r.value === null) return <>None</>;
  return (
    <>
      <b>{pct(r.value)}</b>
      {r.ci95 ? ` [${pct(r.ci95[0])}, ${pct(r.ci95[1])}]` : ""} <span className={styles.count}>({r.num}/{r.den})</span>
    </>
  );
}

function agreementText(a: Agreement | null): string {
  if (!a) return "None";
  return `${a.agreement === null ? "None" : pct(a.agreement)} agree, kappa ${a.kappa === null ? "undefined" : a.kappa.toFixed(2)} (n = ${a.n})`;
}

/** One reference's recall: window and anywhere, standard and with extended, the human ceiling beside it. */
function ReferenceTable({ r, ceiling, usgs }: { r: SuiteBReference; ceiling: SuiteBRecall | null; usgs: boolean }) {
  const rows: { id: string; name: string; tag: string; lullStd: Ratio; lullExt: Ratio; human: Ratio | null }[] = [
    {
      id: "window",
      name: "Within the window",
      tag: usgs ? "pre-registered, equal weight" : "pre-registered",
      lullStd: r.recall.standardWindow,
      lullExt: r.recall.bothWindow,
      human: ceiling?.standardWindow ?? null,
    },
    {
      id: "anywhere",
      name: "Anywhere in the video",
      tag: usgs ? "pre-registered, equal weight" : "supplementary",
      lullStd: r.recall.standardAnywhere,
      lullExt: r.recall.bothAnywhere,
      human: ceiling?.standardAnywhere ?? null,
    },
  ];
  return (
    <div className={styles.tableWrap} tabIndex={0} role="group" aria-label={`${referenceName(r.id)}, scrolls sideways on small screens`}>
      <table className={styles.table} data-reference={r.id}>
        <caption className={styles.chartCaption}>
          {referenceName(r.id)}: {r.recall.facts} facts in {r.recall.lines} lines
        </caption>
        <thead>
          <tr>
            <th scope="col">Facts conveyed</th>
            <th scope="col">Lull, standard track</th>
            <th scope="col">Lull, with extended</th>
            <th scope="col">Another human describer</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} data-row={row.id}>
              <th scope="row">
                {row.name} <span className={styles.tag}>{row.tag}</span>
              </th>
              <td className={styles.num} data-cell="lull-standard">
                <Share r={row.lullStd} />
              </td>
              <td className={styles.num} data-cell="lull-extended">
                <Share r={row.lullExt} />
              </td>
              <td className={styles.num} data-cell="human">
                {row.human ? (
                  <>
                    <Share r={row.human} /> <span className={styles.tag}>supplementary</span>
                  </>
                ) : (
                  "None: one describer for this video"
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function OverlapTable({ caption, rows, freeze, ownSpans, usgs }: { caption: string; rows: { video: string; s: OverlapSide }[]; freeze: { video: string; n: number | null }[]; ownSpans: boolean; usgs: { video: string; o: NonNullable<SuiteB["videos"][number]["usgsOwnTimeline"]> }[] }) {
  return (
    <div className={styles.tableWrap} tabIndex={0} role="group" aria-label={`${caption}, scrolls sideways on small screens`}>
      <table className={styles.table}>
        <caption className={styles.chartCaption}>{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Video and track</th>
            <th scope="col">Description</th>
            <th scope="col">Over speech</th>
            <th scope="col">Share</th>
            <th scope="col">Note</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ video, s }) => (
            <tr key={`${video}-${s.side}`} data-overlap={`${video}:${s.side}`}>
              <th scope="row">
                <span className={styles.mono}>{video}</span> {sideName(s.side)}
              </th>
              <td className={styles.num}>{s.descriptionS.toFixed(1)} s</td>
              <td className={styles.num}>{s.overlapS.toFixed(1)} s</td>
              <td className={styles.num}>{s.share === null ? "n/a" : pct(s.share)}</td>
              <td>
                {s.side === "lull_standard" && ownSpans
                  ? "0 by construction against its own speech detection"
                  : s.side === "pfeiffer_vtt"
                    ? "display time, not voiced"
                    : s.side === "hunt_vtt"
                      ? "display time (his voice: the next row)"
                      : s.side === "hunt_wav"
                        ? "voiced"
                        : ""}
              </td>
            </tr>
          ))}
          {freeze
            .filter((f) => f.n !== null)
            .map((f) => (
              <tr key={`${f.video}-freeze`} data-overlap={`${f.video}:freeze`}>
                <th scope="row">
                  <span className={styles.mono}>{f.video}</span> Lull, extended freeze points inside speech
                </th>
                <td className={styles.num} />
                <td className={styles.num}>{f.n}</td>
                <td className={styles.num} />
                <td>the picture pauses between words, mid-sentence</td>
              </tr>
            ))}
          {usgs.map(({ video, o }) => (
            <tr key={`${video}-usgs`} data-overlap={`${video}:usgs_ad_own_timeline`}>
              <th scope="row">
                <span className={styles.mono}>{video}</span> USGS AD on its own timeline
              </th>
              <td className={styles.num}>{o.descriptionS.toFixed(1)} s</td>
              <td className={styles.num}>{o.overlapS.toFixed(1)} s</td>
              <td className={styles.num}>{o.share === null ? "n/a" : pct(o.share)}</td>
              <td>
                Not comparable: it lives in pauses the AD version inserts
                {o.narrationMissingS !== null ? `; original narration missing from the AD version ${o.narrationMissingS.toFixed(1)} s` : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Suite B: Lull against human audio description of the same videos. Recall of the describers'
 * facts, each next to the human-to-human ceiling where two people described the same film;
 * the judges' agreement; talking over speech; how many words each side used.
 */
export function SuiteBView({ b }: { b: SuiteB }) {
  const usgsRefs = new Set(["usgs_ad"]);
  const ownRows = b.videos.flatMap((v) => v.ownSpans.sides.map((s) => ({ video: v.video, s })));
  const refRows = b.videos.flatMap((v) => (v.againstReference ? v.againstReference.sides.map((s) => ({ video: v.video, s })) : []));
  return (
    <section aria-labelledby="suite-b-title" className={styles.section} data-suite="b">
      <h2 id="suite-b-title" className={styles.h2}>
        Suite B: against human audio description
      </h2>
      {!b.complete || b.smoke ? (
        <p className={styles.partial} data-partial="">
          {b.note ?? "A partial or smoke run: not the suite's results."}
        </p>
      ) : null}
      <dl className={styles.facts}>
        <div>
          <dt>Pre-registration</dt>
          <dd>
            Tag <span className={styles.mono}>{b.preregistration.tag}</span>, commit <span className={styles.mono}>{short(b.preregistration.commit)}</span>,{" "}
            {day(b.preregistration.committed)}
            {b.firstPreregistrationTag ? ` (first pinned as ${b.firstPreregistrationTag})` : ""}
          </dd>
        </div>
        <div>
          <dt>Reference set sha256</dt>
          <dd className={styles.mono}>{b.referenceSetSha256}</dd>
        </div>
        <div>
          <dt>Scored at</dt>
          <dd>
            Commit <span className={styles.mono}>{short(b.gitCommit)}</span>, {day(b.date)}
          </dd>
        </div>
        <div>
          <dt>Runs scored</dt>
          <dd>
            {b.runs.map((r, i) => (
              <span key={r.video}>
                {i ? "; " : ""}
                <span className={styles.mono}>{r.video}</span> {r.clip ? `0–${r.clip[1].toFixed(0)} s` : ""}
                {r.engine ? `, engine ${r.engine}` : ""}
              </span>
            ))}
          </dd>
        </div>
        <div>
          <dt>Judges</dt>
          <dd>
            {b.judgeModel ? modelName(b.judgeModel) : "None recorded"}
            {b.secondJudgeModel ? `, and a second judge, ${b.secondJudgeModel}` : ""}
            {b.windowS !== null ? `. A fact counts when a line conveys it within ±${b.windowS} s of where the describer said it` : ""}
            {b.longBlockWindowS !== null ? ` (±${b.longBlockWindowS} s for a block over ${b.longBlockWindowS} s)` : ""}.
          </dd>
        </div>
        {b.spendUsd !== null ? (
          <div>
            <dt>Spent on scoring</dt>
            <dd>
              ${b.spendUsd.toFixed(4)}
              {b.callsAfterPreregistration ? "; every scoring call came after the pre-registration" : ""}
            </dd>
          </div>
        ) : null}
      </dl>

      <h3 className={styles.h3}>How much of what the describers said, Lull conveyed</h3>
      <p className={styles.lede}>
        Each share has its 95 % interval over the reference lines. On Elephants Dream two people described the same film, so each is also scored
        against the other: that is the human ceiling shown next to Lull’s number. USGS videos have one describer each, so there is no ceiling there,
        and the window and anywhere shares carry equal weight (a USGS anchor is the AD version’s freeze point).
      </p>
      {b.references.map((r) => (
        <div key={r.id} className={styles.reference}>
          <ReferenceTable r={r} ceiling={humanCeiling(b, r.id)} usgs={usgsRefs.has(r.id)} />
          {r.flaggedFacts > 0 && r.withoutUnresolved ? (
            <p className={styles.note} data-lower-bound={r.id}>
              {r.flaggedFacts} facts still hold a term the speech recogniser may have misheard. Without them: within the window, with extended,{" "}
              <Share r={r.withoutUnresolved.bothWindow} />. With them counted, the all-facts recall above is a lower bound.
            </p>
          ) : null}
        </div>
      ))}

      <h3 className={styles.h3}>The second judge</h3>
      <p className={styles.added}>Supplementary: reported beside the primary judge, never instead of it.</p>
      <div className={styles.tableWrap} tabIndex={0} role="group" aria-label="The second judge, scrolls sideways on small screens">
        <table className={styles.table}>
          <caption className={styles.chartCaption}>{b.secondJudgeModel ?? "Second judge"}: Lull’s recall, within the window, with extended, and agreement with the primary judge</caption>
          <thead>
            <tr>
              <th scope="col">Reference</th>
              <th scope="col">Recall, second judge</th>
              <th scope="col">Agreement, window</th>
              <th scope="col">Agreement, anywhere</th>
            </tr>
          </thead>
          <tbody>
            {b.references.map((r) => (
              <tr key={r.id} data-second-judge={r.id}>
                <th scope="row">{referenceName(r.id)}</th>
                <td className={styles.num}>
                  <Share r={r.secondJudge?.recall?.bothWindow ?? null} />
                </td>
                <td>{agreementText(r.secondJudge?.agreementWindow ?? null)}</td>
                <td>{agreementText(r.secondJudge?.agreementAnywhere ?? null)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {b.probe.primary || b.probe.second ? (
        <p className={styles.note}>
          {[b.probe.primary, b.probe.second]
            .filter((p): p is NonNullable<typeof p> => p !== null)
            .map(
              (p) =>
                `Judge probe, ${p.model}: positives credited ${p.sensitivity ? `${p.sensitivity[0]}/${p.sensitivity[1]}` : "None"}, near misses rejected ${p.specificity ? `${p.specificity[0]}/${p.specificity[1]}` : "None"}${p.crossCredits !== null ? `, cross-credits ${p.crossCredits}` : ""}.`,
            )
            .join(" ")}
        </p>
      ) : null}

      <h3 className={styles.h3}>Talking over speech</h3>
      {refRows.length ? (
        <OverlapTable
          caption="Against the reference transcript of the original (same recogniser, independent of the Lull run)"
          rows={refRows}
          freeze={b.videos.map((v) => ({ video: v.video, n: v.againstReference?.freezePointsInSpeech ?? null }))}
          ownSpans={false}
          usgs={[]}
        />
      ) : null}
      <OverlapTable
        caption="Against the Lull run’s own speech detection (the same spans for every track)"
        rows={ownRows}
        freeze={[]}
        ownSpans
        usgs={b.videos.flatMap((v) => (v.usgsOwnTimeline ? [{ video: v.video, o: v.usgsOwnTimeline }] : []))}
      />

      {b.videos.some((v) => v.density) ? (
        <>
          <h3 className={styles.h3}>Words per minute of video</h3>
          <div className={styles.tableWrap} tabIndex={0} role="group" aria-label="Words per minute, scrolls sideways on small screens">
            <table className={styles.table}>
              <caption className={styles.chartCaption}>How much each side says</caption>
              <thead>
                <tr>
                  <th scope="col">Video and side</th>
                  <th scope="col">Words</th>
                  <th scope="col">Per minute</th>
                </tr>
              </thead>
              <tbody>
                {b.videos.flatMap((v) =>
                  (v.density?.sides ?? []).map((d) => (
                    <tr key={`${v.video}-${d.side}`} data-density={`${v.video}:${d.side}`}>
                      <th scope="row">
                        <span className={styles.mono}>{v.video}</span> {sideName(d.side)}
                      </th>
                      <td className={styles.num}>{d.words}</td>
                      <td className={styles.num}>{d.perMin.toFixed(1)}</td>
                    </tr>
                  )),
                )}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
    </section>
  );
}
