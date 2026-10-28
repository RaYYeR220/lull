"use client";

import { useEffect, useState, type ReactNode } from "react";
import { BenchmarksError, parseBenchmarks, type Agreement, type Benchmarks, type Count, type SuiteA } from "@/lib/benchmarks-schema";
import { modelName } from "@/lib/run-format";
import { IntervalChart } from "./IntervalChart";
import styles from "./Benchmarks.module.css";

type State = { kind: "loading" } | { kind: "none" } | { kind: "broken"; reason: string } | { kind: "ready"; data: Benchmarks };

const pct = (x: number | null, digits = 1) => (x === null ? "None" : `${(x * 100).toFixed(digits)} %`);
const frac = (c: Count | null) => (c ? `${c[0]} of ${c[1]}` : "None");
const usd = (x: number | null, digits = 3) => (x === null ? "None" : `$${x.toFixed(digits)}`);
const day = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("en-GB", { day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC", timeZoneName: "short" });
};
const short = (sha: string) => sha.slice(0, 12);

function Stat({ name, value, detail, id }: { name: string; value: string; detail: string; id: string }) {
  return (
    <div className={styles.stat} data-stat={id}>
      <dt>{name}</dt>
      <dd className={styles.statValue}>{value}</dd>
      <dd className={styles.statDetail}>{detail}</dd>
    </div>
  );
}

function Method({ a }: { a: SuiteA }) {
  return (
    <dl className={styles.facts}>
      <div>
        <dt>Pre-registration</dt>
        <dd>
          Tag <span className={styles.mono}>{a.preregistration.tag}</span>, commit <span className={styles.mono}>{short(a.preregistration.commit)}</span>,{" "}
          {day(a.preregistration.committed)}
        </dd>
      </div>
      <div>
        <dt>Answer key sha256</dt>
        <dd className={styles.mono}>{a.keySha256}</dd>
      </div>
      <div>
        <dt>Scored at</dt>
        <dd>
          Commit <span className={styles.mono}>{short(a.gitCommit)}</span>
          {a.gitDirty ? " (with uncommitted changes)" : ""}, {day(a.date)}
        </dd>
      </div>
      {a.callsAfterPreregistration !== null ? (
        <div>
          <dt>Model calls after the key</dt>
          <dd>{a.callsAfterPreregistration ? "Every call the results use came after the pre-registration commit." : "Some calls the results use came before the pre-registration commit."}</dd>
        </div>
      ) : null}
      <div>
        <dt>What was described</dt>
        <dd>
          {a.aggregate.videos} {a.aggregate.videos === 1 ? "lecture" : "lectures"}, {a.aggregate.minutes.toFixed(1)} minutes
          {a.aggregate.silenceS !== null ? `, ${a.aggregate.silenceS.toFixed(1)} s of it without narration` : ""}
          {a.aggregate.pauses !== null ? ` in ${a.aggregate.pauses} pauses (${a.aggregate.pausesGe2s ?? 0} of at least 2 s)` : ""}
          {a.runOptions ? `; ${a.runOptions.mode ?? "standard"} mode, ${a.runOptions.profile ?? ""} profile, voice ${a.runOptions.voice ?? ""}, research ${a.runOptions.research ? "on" : "off"}` : ""}.
        </dd>
      </div>
      <div>
        <dt>Judges</dt>
        <dd>
          {a.judgeModel ? modelName(a.judgeModel) : "None recorded"}
          {a.secondJudge ? `, and a second judge, ${a.secondJudge.model}` : ""}.{a.judgeNote ? ` Note: ${a.judgeNote}.` : ""}
        </dd>
      </div>
      {a.engineModels.length ? (
        <div>
          <dt>Engine models</dt>
          <dd>{a.engineModels.map(modelName).join(", ")}</dd>
        </div>
      ) : null}
      {a.spendUsd !== null ? (
        <div>
          <dt>Spent on the evaluation</dt>
          <dd>{usd(a.spendUsd, 4)}, the runs and the judges together</dd>
        </div>
      ) : null}
    </dl>
  );
}

function AgreementRow({ name, a }: { name: string; a: Agreement | null }) {
  if (!a) return null;
  return (
    <tr>
      <th scope="row">{name}</th>
      <td className={styles.num}>{a.n}</td>
      <td className={styles.num}>{a.agreement === null ? "None" : pct(a.agreement)}</td>
      {a.kappa === null ? <td>Undefined: neither judge varied</td> : <td className={styles.num}>{a.kappa.toFixed(2)}</td>}
    </tr>
  );
}

function SuiteAView({ a }: { a: SuiteA }) {
  const g = a.aggregate;
  const hw = a.hardware;
  return (
    <>
      {!a.complete ? (
        <p className={styles.partial} data-partial="">
          {a.note ? `${a.note.charAt(0).toUpperCase()}${a.note.slice(1)}.` : "A partial run: not the suite's results."} The numbers below cover {g.videos}{" "}
          {g.videos === 1 ? "video" : "videos"} only.
        </p>
      ) : null}

      <section aria-labelledby="how-title" className={styles.section}>
        <h2 id="how-title" className={styles.h2}>
          What was measured, and how
        </h2>
        <Method a={a} />
      </section>

      <section aria-labelledby="primary-title" className={styles.section}>
        <h2 id="primary-title" className={styles.h2}>
          Pre-registered measures
        </h2>
        <p className={styles.lede}>Each share is pooled over the videos, with a 95 % percentile bootstrap interval (10,000 resamples of videos).</p>
        <IntervalChart
          caption="Shares, with 95 % intervals"
          rows={[
            { id: "recall_standard", name: "Visual facts described, standard track", what: "Facts on screen that the narration leaves unsaid. Higher is better.", ratio: g.recallStandard },
            { id: "recall_with_extended", name: "Visual facts described, with the extended track", what: "The same, counting the lines the picture holds for. Higher is better.", ratio: g.recallWithExtended },
            ...(g.timelyRecallWithExtended
              ? [{ id: "timely", name: "Described while the slide is up", what: "With the extended track, a line that starts while its slide shows. Higher is better.", ratio: g.timelyRecallWithExtended }]
              : []),
            { id: "hallucination_claims", name: "Unsupported claims", what: "Claims of published lines the answer key doesn't support. Lower is better.", ratio: g.hallucinationClaims },
            { id: "redundancy_cues", name: "Lines that only restate the narration", what: "Published lines that add nothing to what is said. Lower is better.", ratio: g.redundancyCues },
          ]}
        />
        <dl className={styles.stats}>
          <Stat
            id="overlap"
            name="Over narration"
            value={`${g.overlapSTotal.toFixed(3)} s`}
            detail={`Seconds of description audio inside narration, all videos together; must be 0.${g.extendedFreezeInSpeech !== null ? ` Extended freeze points inside speech: ${g.extendedFreezeInSpeech}.` : ""}${g.capacityViolations !== null ? ` Lines past their pause: ${g.capacityViolations}.` : ""}`}
          />
          <Stat
            id="decoys"
            name="Decoys stated"
            value={`${g.decoysStated} of ${g.decoysTotal}`}
            detail={`Facts planted as absent that a line states anyway; lower is better.${g.decoyNumberHits !== null ? ` Decoy-only numbers in a line: ${g.decoyNumberHits}.` : ""}`}
          />
          <Stat
            id="cost"
            name="Model spend per minute"
            value={usd(g.cost.modelUsdPerMin)}
            detail={`${usd(g.cost.modelUsd, 4)} in all${g.cost.perMinCi95 ? `; 95 % interval ${usd(g.cost.perMinCi95[0])} to ${usd(g.cost.perMinCi95[1])} per minute (model and web searches together)` : ""}. Web searches apart: ${usd(g.cost.tavilyUsdPerMin)} per minute.`}
          />
          <Stat
            id="speed"
            name="Processing per minute of video"
            value={g.processing.sPerMin === null ? "None" : `${g.processing.sPerMin.toFixed(1)} s`}
            detail={`${g.processing.seconds.toFixed(1)} s in all${g.processing.sPerMinCi95 ? `; 95 % interval ${g.processing.sPerMinCi95[0].toFixed(1)} to ${g.processing.sPerMinCi95[1].toFixed(1)} s` : ""}.${hw ? ` On ${[hw.cpu, ...hw.gpus].filter(Boolean).join(", ")}${hw.os ? ` (${hw.os})` : ""}; ${hw.models ?? "models remote"}, speech ${hw.speechServer?.includes("local") ? "local" : (hw.speechServer ?? "")}.` : ""}`}
          />
        </dl>
      </section>

      <section aria-labelledby="supplementary-title" className={styles.section}>
        <h2 id="supplementary-title" className={styles.h2}>
          Supplementary measures
        </h2>
        <p className={styles.added} data-supplementary="">
          Added after the pre-registration. They are reported beside the pre-registered measures, never instead of them.
        </p>
        <IntervalChart
          caption="Supplementary shares, with 95 % intervals"
          rows={[
            ...(g.recallTieAwareStandard ? [{ id: "tie_standard", name: "Visual facts, a tied highest value counted", what: "Standard track; a chart value tied at the top is credited with any of its categories.", ratio: g.recallTieAwareStandard }] : []),
            ...(g.recallTieAwareWithExtended ? [{ id: "tie_extended", name: "The same, with the extended track", what: "Higher is better.", ratio: g.recallTieAwareWithExtended }] : []),
            ...(g.hallucinationVoicedText ? [{ id: "voiced", name: "Unsupported statements in the voiced text", what: "Judged against what is on screen while the line plays, so timing counts. Lower is better.", ratio: g.hallucinationVoicedText }] : []),
            ...(g.voicedUnverifiable ? [{ id: "unverifiable", name: "Unverifiable statements in the voiced text", what: "Neither supported nor contradicted by what is on screen.", ratio: g.voicedUnverifiable }] : []),
            ...(g.voicedCuesWithUnsupported ? [{ id: "voiced_cues", name: "Lines with an unsupported statement", what: "Lower is better.", ratio: g.voicedCuesWithUnsupported }] : []),
            ...(g.claimsNotInCueText ? [{ id: "not_in_text", name: "Claims the line's text doesn't say", what: "Claims in the run's split that the voiced words don't carry.", ratio: g.claimsNotInCueText }] : []),
            ...(g.hallucinationClaimsInCueText ? [{ id: "said_unsupported", name: "Unsupported, among claims the text says", what: "Lower is better.", ratio: g.hallucinationClaimsInCueText }] : []),
          ]}
        />
        {g.decoysAbsentPart !== null ? (
          <p className={styles.note}>
            Decoys stated in their absent part only: <b>{g.decoysAbsentPart}</b>.
          </p>
        ) : null}
      </section>

      {a.videos.length ? (
        <section aria-labelledby="videos-title" className={styles.section}>
          <h2 id="videos-title" className={styles.h2}>
            Every video
          </h2>
          <div className={styles.tableWrap} tabIndex={0} role="group" aria-label="Every video, scrolls sideways on small screens">
            <table className={styles.table}>
              <thead>
                <tr>
                  <th scope="col">Video</th>
                  <th scope="col">Length</th>
                  <th scope="col">Without narration</th>
                  <th scope="col">Pauses (2 s or more)</th>
                  <th scope="col">Facts, standard</th>
                  <th scope="col">Facts, with extended</th>
                  <th scope="col">Unsupported claims</th>
                  <th scope="col">Over narration</th>
                  <th scope="col">Model spend</th>
                  <th scope="col">Processing</th>
                </tr>
              </thead>
              <tbody>
                {a.videos.map((v) => (
                  <tr key={v.id} data-video={v.id}>
                    <th scope="row">
                      <span className={styles.mono}>{v.id}</span> {v.subject}
                      {v.failed ? <span className={styles.failed}> Failed: {v.failed}</span> : null}
                    </th>
                    <td className={styles.num}>{v.durationS.toFixed(1)} s</td>
                    <td className={styles.num}>{v.silence ? `${v.silence.seconds.toFixed(1)} s` : "None"}</td>
                    <td className={styles.num}>{v.silence ? `${v.silence.pauses} (${v.silence.pausesGe2s})` : "None"}</td>
                    <td className={styles.num}>{frac(v.recallStandard)}</td>
                    <td className={styles.num}>{frac(v.recallWithExtended)}</td>
                    <td className={styles.num}>{frac(v.hallucinationClaims)}</td>
                    <td className={styles.num}>{v.overlapS === null ? "None" : `${v.overlapS.toFixed(3)} s`}</td>
                    <td className={styles.num}>{usd(v.costModelUsd, 4)}</td>
                    <td className={styles.num}>{v.processingS === null ? "None" : `${v.processingS.toFixed(1)} s`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      {a.secondJudge || a.probe ? (
        <section aria-labelledby="judges-title" className={styles.section}>
          <h2 id="judges-title" className={styles.h2}>
            How far to trust the judges
          </h2>
          {a.secondJudge ? (
            <>
              <p className={styles.lede}>
                A second judge from another model family, {a.secondJudge.model}, made the same calls. Its recall {pct(a.secondJudge.recall?.value ?? null)}, unsupported
                claims {pct(a.secondJudge.hallucination?.value ?? null)}
                {a.secondJudge.hallucinationVoicedText ? `, unsupported voiced statements ${pct(a.secondJudge.hallucinationVoicedText.value)}` : ""}.
              </p>
              <table className={styles.table}>
                <caption className={styles.chartCaption}>Agreement between the two judges</caption>
                <thead>
                  <tr>
                    <th scope="col">On</th>
                    <th scope="col">Items</th>
                    <th scope="col">Agreement</th>
                    <th scope="col">Cohen’s kappa</th>
                  </tr>
                </thead>
                <tbody>
                  <AgreementRow name="Visual facts" a={a.secondJudge.agreement.facts} />
                  <AgreementRow name="Claims" a={a.secondJudge.agreement.claims} />
                  <AgreementRow name="Voiced lines" a={a.secondJudge.agreement.voicedCues} />
                </tbody>
              </table>
            </>
          ) : null}
          {a.probe ? (
            <dl className={styles.facts}>
              <div>
                <dt>Probe: planted facts credited</dt>
                <dd>{frac(a.probe.facts)} facts and {frac(a.probe.decoys)} decoys credited to the synthetic line that states them</dd>
              </div>
              <div>
                <dt>Probe: near misses</dt>
                <dd>{frac(a.probe.negativesNotCredited)} near misses (a wrong value, a swapped word, a gist) not credited</dd>
              </div>
              {a.probe.voicedSensitivity ? (
                <div>
                  <dt>Probe: voiced text</dt>
                  <dd>
                    sensitivity {frac(a.probe.voicedSensitivity)}, specificity {frac(a.probe.voicedSpecificity)}
                  </dd>
                </div>
              ) : null}
            </dl>
          ) : null}
        </section>
      ) : null}
    </>
  );
}

/**
 * The evaluation as published in public/benchmarks.json: method first, every share with its
 * interval, supplementary measures labelled as added later, each video, the judges' agreement
 * and the known failures. With no file yet it says so. Test data says so on every view.
 */
export function BenchmarksView({ initial, readme, empty }: { initial: Benchmarks | null; readme: string; empty: ReactNode }) {
  const [state, setState] = useState<State>(initial ? { kind: "ready", data: initial } : { kind: "loading" });

  // a site built before the results existed still picks them up once the file is published
  useEffect(() => {
    if (initial) return;
    const ctl = new AbortController();
    fetch("/benchmarks.json", { signal: ctl.signal, cache: "no-store" })
      .then(async (res) => {
        if (res.status === 404) return setState({ kind: "none" });
        if (!res.ok) throw new Error(`the file answered ${res.status}`);
        setState({ kind: "ready", data: parseBenchmarks(await res.json()) });
      })
      .catch((e: unknown) => {
        if (ctl.signal.aborted) return;
        setState({ kind: "broken", reason: e instanceof BenchmarksError || e instanceof Error ? e.message : String(e) });
      });
    return () => ctl.abort();
  }, [initial]);

  if (state.kind === "loading") return <p className={styles.loading}>Looking for the published results…</p>;
  if (state.kind === "none") return <>{empty}</>;
  if (state.kind === "broken")
    return (
      <>
        <p className={styles.partial} role="status">
          The published results couldn’t be read: {state.reason}
        </p>
        {empty}
      </>
    );

  const b = state.data;
  return (
    <div className={styles.results}>
      {b.fixture ? (
        <p className={styles.fixture} data-fixture="">
          <b>Test fixture.</b> These numbers are test data for this site’s own tests, not the published evaluation.
        </p>
      ) : null}
      <p className={styles.generated}>
        Published {day(b.generatedAt)} from commit <span className={styles.mono}>{short(b.gitCommit)}</span>.{" "}
        <a href={readme} className={styles.link}>
          The method in full
        </a>
      </p>
      {b.suiteA ? <SuiteAView a={b.suiteA} /> : <p className={styles.lede}>No suite has been scored yet.</p>}
      {b.otherSuites.length ? (
        <p className={styles.note}>
          Also in the file, not drawn here yet: {b.otherSuites.map((s) => s.replace("_", " ")).join(", ")}.
        </p>
      ) : null}
      <section aria-labelledby="failures-title" className={styles.section}>
        <h2 id="failures-title" className={styles.h2}>
          Known failures
        </h2>
        {b.knownFailures.length ? (
          <ul className={styles.failures}>
            {b.knownFailures.map((f) => (
              <li key={f.title}>
                <h3 className={styles.h3}>{f.title}</h3>
                <p>{f.detail}</p>
              </li>
            ))}
          </ul>
        ) : (
          <p className={styles.lede}>None listed with these results.</p>
        )}
      </section>
    </div>
  );
}
