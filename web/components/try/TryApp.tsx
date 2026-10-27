"use client";

import { useCallback, useEffect, useMemo, useReducer, useRef, useState, type ReactNode } from "react";
import { createAnnouncer, type Announcer } from "@/lib/announce";
import {
  ApiError,
  STAGES,
  followRun,
  health,
  isAbort,
  saveEditToken,
  samples,
  startRun,
  type RunEvent,
  type RunInput,
  type RunOptions,
  type StageName,
} from "@/lib/api";
import type { Recorded } from "@/lib/recorded";
import { STAGE_COPY } from "@/lib/run-format";
import { CueNumbers, initialRun, runReducer, type RunSource } from "@/lib/run-state";
import { RefusalNotice } from "./RefusalNotice";
import { RunView } from "./RunView";
import { ServiceStatus, type Service } from "./ServiceStatus";
import { SourcePicker, type SampleItem } from "./SourcePicker";
import styles from "./TryApp.module.css";

/** The API's run id pattern (store.RUN_ID_RE). */
const RUN_ID = /^[A-Za-z0-9._-]{1,80}$/;

const isStage = (s: string | null): s is StageName => s !== null && (STAGES as readonly string[]).includes(s);

/** Keeps the run in the address (?run=<id>), so a reload follows it again. */
function setRunParam(id: string | null) {
  try {
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("run", id);
    else url.searchParams.delete("run");
    window.history.replaceState(null, "", url);
  } catch {
    // an address the browser won't rewrite: the run still streams
  }
}

type Props = {
  /** The page heading and lede, rendered on the server. */
  intro: ReactNode;
  /** Recorded runs shipped with the site: offered whenever a live run can't happen. */
  recorded: Recorded[];
};

/**
 * The try-it page's state: the live service, the picker, and one run at a time streamed from
 * the API. Every event goes through one handler that updates the view and, sparingly, the
 * polite live region.
 */
export function TryApp({ intro, recorded }: Props) {
  const [run, dispatch] = useReducer(runReducer, undefined, initialRun);
  const [service, setService] = useState<Service>({ status: "loading" });
  const [sampleIds, setSampleIds] = useState<string[] | null>(null);

  const ctrl = useRef<AbortController | null>(null);
  const seen = useRef(-1);
  const numbers = useRef(new CueNumbers());
  const announcer = useRef<Announcer | null>(null);
  const region = useRef<HTMLParagraphElement>(null);
  const runHeading = useRef<HTMLHeadingElement>(null);
  const pickHeading = useRef<HTMLHeadingElement>(null);
  const last = useRef<{ input: RunInput; source: RunSource; opts: RunOptions } | null>(null);
  /** Where focus goes once the view has changed after the visitor's own action. */
  const focusNext = useRef<"run" | "pick" | null>(null);

  useEffect(() => {
    const a = createAnnouncer((text) => {
      if (region.current) region.current.textContent = text;
    });
    announcer.current = a;
    return () => a.dispose();
  }, []);

  const checkService = useCallback(async () => {
    const signal = AbortSignal.timeout(10_000);
    const [h, s] = await Promise.allSettled([health(signal), samples(signal)]);
    setService(h.status === "fulfilled" ? { status: "ok", health: h.value } : { status: "down" });
    setSampleIds((prev) => (s.status === "fulfilled" ? s.value.map((x) => x.id) : (prev ?? [])));
  }, []);

  const recheck = useCallback(() => {
    setService({ status: "loading" });
    void checkService();
  }, [checkService]);

  /** One event: dropped if already seen, then into the view and (sparingly) the live region. */
  const handleEvent = useCallback((ev: RunEvent) => {
    if (ev.seq !== null) {
      if (ev.seq <= seen.current) return;
      seen.current = ev.seq;
    }
    const label = ev.type === "cue" ? numbers.current.label(ev) : undefined;
    dispatch({ type: "event", ev, at: Date.now(), label });
    const a = announcer.current;
    switch (ev.type) {
      case "run":
        if (ev.edit_token) saveEditToken(ev.run_id, ev.edit_token);
        setRunParam(ev.run_id);
        if (ev.resumed) a?.stage("The run picks up where it stopped.");
        break;
      case "stage_start":
        if (isStage(ev.stage)) a?.stage(`${STAGE_COPY[ev.stage].doing}.`);
        break;
      case "cue":
        if (label) a?.cue({ n: label.n, extended: label.extended, status: ev.status, text: ev.text, note: ev.note, start: ev.start });
        break;
      case "error":
        a?.now(`The run stopped. ${ev.message}`);
        break;
      case "done": {
        const c = ev.summary?.cues ?? {};
        const voiced = (c.verified ?? 0) + (c.edited ?? 0) + (c.approved ?? 0);
        a?.now(`Finished. ${voiced} ${voiced === 1 ? "line" : "lines"} passed${c.held ? `, ${c.held} held` : ""}. The result follows the run heading.`);
        break;
      }
      default:
        break;
    }
  }, []);

  const follow = useCallback(
    async (runId: string, signal: AbortSignal) => {
      dispatch({ type: "follow", runId, at: Date.now() });
      try {
        const end = await followRun(runId, handleEvent, signal);
        if (end.end === "closed" || end.end === "dropped") {
          dispatch({ type: "lost", error: null });
          announcer.current?.now("The connection to this run was lost.");
        }
      } catch (e) {
        if (isAbort(e)) return;
        dispatch({ type: "lost", error: e instanceof ApiError ? e : null });
        announcer.current?.now("The connection to this run was lost.");
      }
    },
    [handleEvent],
  );

  const start = useCallback(
    async (input: RunInput, source: RunSource, opts: RunOptions) => {
      ctrl.current?.abort();
      const c = new AbortController();
      ctrl.current = c;
      seen.current = -1;
      numbers.current = new CueNumbers();
      last.current = { input, source, opts };
      focusNext.current = "run";
      dispatch({ type: "send", source, at: Date.now() });
      announcer.current?.now(source.kind === "file" ? "Sending the clip." : "Starting the description.");
      try {
        const end = await startRun(input, opts, handleEvent, c.signal);
        if (end.end === "closed" || end.end === "dropped") {
          if (end.runId) await follow(end.runId, c.signal);
          else dispatch({ type: "lost", error: new ApiError({ status: 0, code: "unreachable" }) });
        }
        if (!c.signal.aborted) void checkService(); // the budget moved
      } catch (e) {
        if (isAbort(e)) return;
        dispatch({ type: "refused", error: e instanceof ApiError ? e : new ApiError({ status: 0, code: "server" }) });
        setRunParam(null);
      }
    },
    [handleEvent, follow, checkService],
  );

  const retry = useCallback(() => {
    if (last.current) void start(last.current.input, last.current.source, last.current.opts);
  }, [start]);

  const reconnect = useCallback(() => {
    if (!run.runId) return;
    ctrl.current?.abort();
    const c = new AbortController();
    ctrl.current = c;
    void follow(run.runId, c.signal);
  }, [follow, run.runId]);

  const again = useCallback(() => {
    ctrl.current?.abort();
    ctrl.current = null;
    focusNext.current = "pick";
    dispatch({ type: "reset" });
    setRunParam(null);
  }, []);

  // first visit: ask the service; with ?run=<id>, follow that run
  useEffect(() => {
    void checkService();
  }, [checkService]);

  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("run");
    if (!id || !RUN_ID.test(id)) return;
    const c = new AbortController();
    ctrl.current = c;
    seen.current = -1;
    numbers.current = new CueNumbers();
    dispatch({ type: "reset" });
    void follow(id, c.signal).then(() => {
      if (!c.signal.aborted) void checkService();
    });
    return () => c.abort();
  }, [follow, checkService]);

  // focus follows the visitor's action: into the run they started, back to the picker they asked for
  // (a refusal moves focus to its own notice)
  useEffect(() => {
    const target = focusNext.current;
    if (target === "run" && run.phase !== "idle") {
      focusNext.current = null;
      runHeading.current?.focus();
    } else if (target === "pick" && run.phase === "idle") {
      focusNext.current = null;
      pickHeading.current?.focus();
    }
  }, [run.phase]);

  useEffect(() => {
    const live = ctrl;
    return () => live.current?.abort();
  }, []);

  const items = useMemo<SampleItem[] | null>(() => {
    if (sampleIds === null) return null;
    const byId = new Map(recorded.map((r) => [r.id, r]));
    return sampleIds.map((id) => {
      const r = byId.get(id);
      return { id, title: r?.title ?? id, credit: r?.credit ?? null, duration: r?.duration ?? null, recorded: !!r };
    });
  }, [sampleIds, recorded]);

  const idle = run.phase === "idle";
  const refusal = run.refusal;
  const retryable = refusal && ["busy", "rate_limited", "uploads_busy", "unavailable", "unreachable", "server"].includes(refusal.code);

  return (
    <>
      <div className={styles.head}>
        <div className={styles.intro}>{intro}</div>
        <div className={styles.status}>
          <ServiceStatus service={service} />
        </div>
      </div>

      <p ref={region} role="status" className="sr-only" />

      <div className={styles.work}>
        {idle && refusal ? (
          <RefusalNotice
            key={`${refusal.code}-${refusal.status}-${refusal.message}`}
            kind={refusal.code}
            scope={refusal.scope}
            message={refusal.message}
            retryAfter={refusal.retryAfter}
            onRetry={retryable ? retry : undefined}
            onReady={() => announcer.current?.now("You can try again now.")}
            recorded={recorded}
            focus
          />
        ) : null}

        {idle && !refusal && service.status === "down" ? (
          <RefusalNotice
            kind="unreachable"
            message="New descriptions can’t start until it answers. Check your connection, or check again in a minute."
            onRetry={recheck}
            retryLabel="Check again"
            recorded={recorded}
          />
        ) : null}

        {/* kept mounted while a run shows, so the choices are still there afterwards */}
        <div hidden={!idle}>
          <SourcePicker
            samples={items}
            service={service.status}
            budget={service.status === "ok" ? service.health.budget : null}
            headingRef={pickHeading}
            onStart={(input, source, opts) => void start(input, source, opts)}
          />
        </div>

        {!idle ? <RunView run={run} recorded={recorded} headingRef={runHeading} onAgain={again} onReconnect={reconnect} /> : null}
      </div>
    </>
  );
}
