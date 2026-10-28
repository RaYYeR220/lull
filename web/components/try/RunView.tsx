"use client";

import { useEffect, useMemo, useState, type Ref } from "react";
import { MetaList } from "@/components/ui/MetaList";
import { STAGES, type StageName } from "@/lib/api";
import { cx } from "@/lib/cx";
import type { Recorded } from "@/lib/recorded";
import { STAGE_COPY, clock, megabytes, noteText, usd } from "@/lib/run-format";
import { refusalWords } from "@/lib/refusal-words";
import { elapsedSeconds, type RunState } from "@/lib/run-state";
import { CallFeed } from "./CallFeed";
import { CueList } from "./CueList";
import { LiveTimeline } from "./LiveTimeline";
import { RefusalNotice, type NoticeKind } from "./RefusalNotice";
import { RunResult } from "./RunResult";
import { StageRail } from "./StageRail";
import shared from "./shared.module.css";
import styles from "./RunView.module.css";

/** Elapsed run time, ticking once a second while the run goes; re-renders only itself. */
function Elapsed({ run }: { run: RunState }) {
  const live = run.phase === "sending" || run.phase === "running";
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!live) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [live]);
  return <>{clock(elapsedSeconds(run, now))}</>;
}

function sourceName(run: RunState): string {
  const src = run.source;
  if (!src) return run.runId ?? "a clip";
  if (src.kind === "sample") return src.title;
  if (src.kind === "file") return src.name;
  return `run ${src.id}`;
}

const VERB: Record<RunState["phase"], string> = {
  idle: "Describing",
  sending: "Sending",
  running: "Describing",
  done: "Described",
  error: "Stopped describing",
  lost: "Describing",
  stopped: "Stopped describing",
};

function failureKind(kind: string): NoticeKind {
  if (kind === "BudgetExceeded") return "budget";
  if (kind === "RunTimeout") return "run_timeout";
  return "failed";
}

type Props = {
  run: RunState;
  recorded: readonly Recorded[];
  headingRef: Ref<HTMLHeadingElement>;
  onAgain: () => void;
  onReconnect: () => void;
  /** This browser holds the run's edit key: it may resume a stopped run. */
  canResume?: boolean;
};

/** A run as it happens: the header, the stages, the timeline and lines, and every model call. */
export function RunView({ run, recorded, headingRef, onAgain, onReconnect, canResume = false }: Props) {
  const current = run.current;
  const activeCalls = useMemo(() => {
    const n: Partial<Record<StageName, number>> = {};
    for (const c of run.calls) if (c.stage && (STAGES as readonly string[]).includes(c.stage)) n[c.stage as StageName] = (n[c.stage as StageName] ?? 0) + 1;
    return n;
  }, [run.calls]);

  const progress = useMemo(() => {
    const p: Partial<Record<StageName, string>> = {};
    for (const s of STAGES) {
      const calls = activeCalls[s];
      if (calls) p[s] = `${calls} model ${calls === 1 ? "call" : "calls"}`;
    }
    if (run.cues.length) p.finalize = `${run.cues.length} ${run.cues.length === 1 ? "line" : "lines"} checked`;
    return p;
  }, [activeCalls, run.cues.length]);

  const notes = useMemo(() => run.warnings.filter((w) => w.kind !== "held"), [run.warnings]);
  const ended = run.phase === "done" || run.phase === "error";
  const title = `${VERB[run.phase]} ${sourceName(run)}`;

  const meta = [
    run.duration !== null ? `${clock(run.duration)} long` : null,
    run.options ? `${run.options.mode === "extended" ? "Extended" : "Standard"} mode, voice ${run.options.voice}` : null,
    run.resumed ? "Resumed after a stop" : null,
    run.runId ? (
      <span key="id">
        Run <span className={cx(shared.mono, styles.id)}>{run.runId}</span>
      </span>
    ) : null,
  ].filter((x) => x !== null);

  return (
    <section aria-labelledby="run-title" className={cx(shared.panel, styles.run)} data-phase={run.phase}>
      <header className={styles.bar}>
        <div className={styles.what}>
          <h2 id="run-title" ref={headingRef} tabIndex={-1} className={styles.title}>
            {title}
          </h2>
          {meta.length ? <MetaList items={meta} label="About this run" className={styles.meta} /> : null}
        </div>
        <dl className={styles.figures}>
          <div>
            <dt>{ended ? "Took" : "Elapsed"}</dt>
            <dd>
              <Elapsed run={run} />
            </dd>
          </div>
          <div>
            <dt>Model spend</dt>
            <dd>{usd(run.cost)}</dd>
          </div>
          <div className={styles.now}>
            <dt>Now</dt>
            <dd>
              {run.phase === "sending"
                ? "Sending"
                : run.phase === "done"
                  ? "Finished"
                  : run.phase === "error"
                    ? "Stopped"
                    : run.phase === "lost"
                      ? "Disconnected"
                      : current
                        ? STAGE_COPY[current].name
                        : "Starting"}
            </dd>
          </div>
        </dl>
      </header>

      {run.phase === "sending" ? (
        <div className={styles.sending}>
          <i className={styles.line} aria-hidden="true" />
          <p>
            {run.source?.kind === "file"
              ? `Sending ${run.source.name} (${megabytes(run.source.bytes)}). The service checks its type, size and length, then the budget, before the run starts.`
              : "The service checks the clip and the budget before the run starts."}
          </p>
        </div>
      ) : null}

      {run.phase === "done" && run.runId ? (
        <RunResult runId={run.runId} summary={run.summary} elapsed={elapsedSeconds(run, Date.now())} cost={run.cost} cues={run.cues} onAgain={onAgain} />
      ) : null}

      {run.phase === "error" && run.error ? (
        <RefusalNotice
          className={styles.notice}
          level={3}
          kind={failureKind(run.error.kind)}
          scope={run.error.scope}
          stageName={run.error.stage && (STAGES as readonly string[]).includes(run.error.stage) ? STAGE_COPY[run.error.stage as StageName].name : null}
          message={run.error.message}
          onRetry={onAgain}
          retryLabel="Describe another clip"
          recorded={recorded}
        />
      ) : null}

      {run.phase === "stopped" ? (
        <RefusalNotice
          className={styles.notice}
          level={3}
          kind="stopped"
          message={run.stopped ?? "The run stopped before it finished."}
          onRetry={canResume ? onReconnect : undefined}
          retryLabel="Resume"
          recorded={recorded}
        />
      ) : null}

      {run.phase === "lost" ? (
        <RefusalNotice
          className={styles.notice}
          level={3}
          heading={run.lost ? refusalWords(run.lost, "follow") : null}
          kind={run.lost?.code === "not_found" ? "not_found" : "lost"}
          message={
            run.lost?.message ??
            "The stream stopped before the run finished. The run may still be going on the service: reconnect to follow it."
          }
          retryAfter={run.lost?.retryAfter}
          onRetry={run.lost?.code === "not_found" ? onAgain : onReconnect}
          retryLabel={run.lost?.code === "not_found" ? "Describe another clip" : "Reconnect"}
          recorded={recorded}
        />
      ) : null}

      <StageRail stages={run.stages} progress={progress} />

      <div className={styles.body}>
        <div className={styles.main}>
          <LiveTimeline
            duration={run.duration}
            listen={run.stages.listen.status}
            heard={run.heard}
            cues={run.cues}
            checking={run.stages.finalize.status === "active"}
            latestId={ended ? null : run.latestCue}
          />
          <CueList cues={run.cues} latestId={ended ? null : run.latestCue} />
          {notes.length ? (
            <section aria-labelledby="notes-title" className={styles.notes}>
              <h3 id="notes-title" className={shared.label}>
                Notes from the run
              </h3>
              <ul>
                {notes.map((w, i) => (
                  <li key={i}>{noteText(w.kind, w.message, w.count)}</li>
                ))}
              </ul>
            </section>
          ) : null}
        </div>
        <div className={styles.side}>
          <CallFeed calls={run.calls} cost={run.cost} />
        </div>
      </div>
    </section>
  );
}
