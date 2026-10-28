import type { Ablation, NegativeControl, NegativeRow } from "@/lib/benchmarks-schema";
import { ABLATION_NAMES, ablationSentence } from "@/lib/benchmarks-text";
import { modelName } from "@/lib/run-format";
import { Share } from "./SuiteBView";
import styles from "./Benchmarks.module.css";

const pct = (x: number) => `${(x * 100).toFixed(1)} %`;
const signed = (x: number) => `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(1)} %`;
const short = (sha: string) => sha.slice(0, 12);
const day = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
};

const RULE_NAMES: Record<string, string> = {
  "another slide": "Another slide of the same lecture",
  "other events, 30 s away": "Other events in the same video, at least 30 s away",
  "other video": "Another video",
};

function RuleRow({ name, r }: { name: string; r: NegativeRow }) {
  return (
    <tr data-rule={name}>
      <th scope="row">{RULE_NAMES[name] ?? name}</th>
      <td className={styles.num}>{r.rows}</td>
      <td className={styles.num}>
        <Share r={r.falseAccept} />
      </td>
      <td className={styles.num}>
        <Share r={r.falseAcceptClaims} />
      </td>
      <td className={styles.num}>
        <Share r={r.trueAccept} />
      </td>
    </tr>
  );
}

/**
 * The controls: the negative control (each published line checked against footage it doesn't
 * describe; how often the verifier still passes it) and the ablation (Suite A run again
 * without the frame check, both arms and their paired difference).
 */
export function ControlsView({ negative, ablation }: { negative: NegativeControl | null; ablation: Ablation | null }) {
  const sentence = ablation ? ablationSentence(ablation) : null;
  const h = negative?.headline;
  return (
    <section aria-labelledby="controls-title" className={styles.section} data-suite="controls">
      <h2 id="controls-title" className={styles.h2}>
        Controls: does the frame check catch a false line?
      </h2>
      {negative && h ? (
        <div data-control="negative">
          <h3 className={styles.h3}>Negative control</h3>
          <p className={styles.lede}>
            Each line that passed its frame check is checked again against footage it doesn’t describe. A false accept is a line the check passes on
            the wrong footage. Pre-registration tag <span className={styles.mono}>{negative.preregistration.tag}</span>, commit{" "}
            <span className={styles.mono}>{short(negative.preregistration.commit)}</span>; run {day(negative.date)} at commit{" "}
            <span className={styles.mono}>{short(negative.gitCommit)}</span>
            {negative.verifier ? `; the verifier ${modelName(negative.verifier.model)}, temperature ${negative.verifier.temperature ?? "as the pipeline sets it"}` : ""}
            {negative.spendUsd !== null ? `; spend $${negative.spendUsd.toFixed(4)}` : ""}.
          </p>
          <dl className={styles.stats}>
            <div className={styles.stat} data-stat="false-accept">
              <dt>False accept{h.upperBound ? ", an upper bound" : ""}</dt>
              <dd className={styles.statValue}>
                <Share r={h.falseAccept} />
              </dd>
              <dd className={styles.statDetail}>
                Over the same-video rules ({h.rules.join("; ")}), {h.rows} lines. An upper bound on the verifier’s failures: a claim may be true of the
                other footage too.
              </dd>
            </div>
            <div className={styles.stat} data-stat="false-accept-claims">
              <dt>Per claim</dt>
              <dd className={styles.statValue}>
                <Share r={h.falseAcceptClaims} />
              </dd>
              <dd className={styles.statDetail}>Claims passed on the wrong footage.</dd>
            </div>
            {h.discriminativeFalseAccept ? (
              <div className={styles.stat} data-stat="discriminative">
                <dt>Discriminative false accept</dt>
                <dd className={styles.statValue}>
                  <Share r={h.discriminativeFalseAccept} />
                </dd>
                <dd className={styles.statDetail}>
                  Suite A lines{h.suiteARows !== null ? ` (${h.suiteARows})` : ""} whose answer key says the other footage can’t show what they state.
                </dd>
              </div>
            ) : null}
            <div className={styles.stat} data-stat="true-accept">
              <dt>True accept, optimistic</dt>
              <dd className={styles.statValue}>
                <Share r={h.trueAccept} />
              </dd>
              <dd className={styles.statDetail}>On the true clip. Optimistic: these lines had already passed verification there.</dd>
            </div>
          </dl>
          <p className={styles.added}>The per-rule rows and the discriminative false accept were added after the pre-registration.</p>
          <div className={styles.tableWrap} tabIndex={0} role="group" aria-label="By mismatch rule, scrolls sideways on small screens">
            <table className={styles.table}>
              <caption className={styles.chartCaption}>By mismatch rule</caption>
              <thead>
                <tr>
                  <th scope="col">The wrong footage</th>
                  <th scope="col">Lines</th>
                  <th scope="col">False accept</th>
                  <th scope="col">Per claim</th>
                  <th scope="col">True accept</th>
                </tr>
              </thead>
              <tbody>
                {negative.byRule.map((r) => (
                  <RuleRow key={r.rule} name={r.rule} r={r} />
                ))}
              </tbody>
            </table>
          </div>
          <p className={styles.note}>Every false accept is listed under Known failures.</p>
        </div>
      ) : null}

      {ablation ? (
        <div data-control="ablation">
          <h3 className={styles.h3}>Ablation: Suite A without the frame check</h3>
          <p className={styles.lede}>
            The twelve Suite A lectures run again with the frame check off, compared with the scored run, video by video
            {ablation.preregistration.controlsTag ? ` (pre-registration ${ablation.preregistration.controlsTag}` : ""}
            {ablation.preregistration.suiteATag ? `, Suite A pin ${ablation.preregistration.suiteATag})` : ablation.preregistration.controlsTag ? ")" : ""}; run {day(ablation.date)}
            {ablation.spendUsd !== null ? `, spend $${ablation.spendUsd.toFixed(4)}` : ""}.
          </p>
          {sentence ? (
            <p className={styles.verdict} data-ablation-sentence="">
              {sentence}
            </p>
          ) : null}
          <div className={styles.tableWrap} tabIndex={0} role="group" aria-label="Ablation, scrolls sideways on small screens">
            <table className={styles.table}>
              <caption className={styles.chartCaption}>With and without the frame check, and the paired difference over videos</caption>
              <thead>
                <tr>
                  <th scope="col">Measure</th>
                  <th scope="col">With the check</th>
                  <th scope="col">Without</th>
                  <th scope="col">Without minus with, 95 % interval</th>
                </tr>
              </thead>
              <tbody>
                {ablation.comparison.map((r) => (
                  <tr key={r.metric} data-ablation={r.metric}>
                    <th scope="row">
                      {ABLATION_NAMES[r.metric] ?? r.metric}
                      {ablation.headline === r.metric ? <span className={styles.tag}> headline</span> : null}
                    </th>
                    <td className={styles.num}>{pct(r.with)}</td>
                    <td className={styles.num}>{pct(r.without)}</td>
                    <td className={styles.num}>
                      {signed(r.difference)}
                      {r.ci95 ? ` [${signed(r.ci95[0])}, ${signed(r.ci95[1])}]` : ""}
                      {r.videos !== null ? <span className={styles.count}> ({r.videos} videos)</span> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {ablation.claimSplit ? (
            <p className={styles.note}>
              Share of lines carrying the verifier’s claim split: with {ablation.claimSplit.with ?? "None"}, without {ablation.claimSplit.without ?? "None"}. Without
              it, the claims measure is judged per whole line; the voiced-text measure splits both arms the same way.
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
