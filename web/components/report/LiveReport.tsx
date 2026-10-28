"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { RunNotice } from "@/components/run/RunNotice";
import { Door, Gate } from "@/components/ui/Gate";
import { ApiError, isAbort } from "@/lib/api";
import { apiReport, type ReportData } from "@/lib/report-data";
import { followHref } from "@/lib/routes";
import { loadRun, type Loaded } from "@/lib/run-load";
import type { ShowcaseIndexEntry } from "@/lib/showcase-schema.mjs";
import { RunReport } from "./RunReport";
import styles from "./Report.module.css";

type State = { kind: "loading" } | { kind: "down"; message: string | null } | Exclude<Loaded, { kind: "done" }> | { kind: "ready"; report: ReportData };

/** A live run's conformance report, loaded from the API; where the run stands when it isn't published. */
export function LiveReport({ id, index }: { id: string; index: readonly ShowcaseIndexEntry[] }) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const ctl = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    ctl.current?.abort();
    const c = new AbortController();
    ctl.current = c;
    setState({ kind: "loading" });
    try {
      const got = await loadRun(id, { index, signal: c.signal });
      if (c.signal.aborted) return;
      setState(got.kind === "done" ? { kind: "ready", report: apiReport(id, got.detail, got.run) } : got);
    } catch (e) {
      if (isAbort(e) || c.signal.aborted) return;
      setState({ kind: "down", message: e instanceof ApiError && e.code !== "unreachable" ? e.message : null });
    }
  }, [id, index]);

  useEffect(() => {
    void load();
    return () => ctl.current?.abort();
  }, [load]);

  const ways = (
    <Gate label="What next">
      <Door tone="lit" href="/try">
        Describe a clip
      </Door>
      <Door tone="dark" href="/watch">
        Watch a recorded run
      </Door>
    </Gate>
  );

  return (
    <>
      <p role="status" className={state.kind === "loading" ? styles.note : "sr-only"}>
        {state.kind === "loading" ? "Loading the run’s report…" : state.kind === "ready" ? "The report is ready." : ""}
      </p>
      {state.kind === "ready" ? (
        <RunReport r={state.report} />
      ) : state.kind === "running" ? (
        <RunNotice
          eyebrow="Conformance report"
          title="This run is still being described."
          doors={
            <Gate label="Follow the run">
              <Door tone="lit" href={followHref(id)}>
                Follow it live
              </Door>
            </Gate>
          }
        >
          <p>Its report is written when it is published.</p>
        </RunNotice>
      ) : state.kind === "stopped" ? (
        <RunNotice eyebrow="Conformance report" title="This run hasn’t finished." doors={ways}>
          <p>Nothing was published from it yet, so there is no report. Its owner can resume it from the try-it page.</p>
        </RunNotice>
      ) : state.kind === "failed" ? (
        <RunNotice eyebrow="Conformance report" title="This run stopped before it finished." doors={ways}>
          {state.reason ? <p>{state.reason}</p> : null}
          <p>Nothing was published from it, so it has no report.</p>
        </RunNotice>
      ) : state.kind === "missing" ? (
        <RunNotice eyebrow="Conformance report" title="There’s no run with that id." doors={ways}>
          <p>It may have been removed: copies made for editing are kept for a day.</p>
        </RunNotice>
      ) : state.kind === "down" ? (
        <RunNotice
          eyebrow="Conformance report"
          title="The live service isn’t answering."
          doors={
            <Gate label="What next">
              <Door tone="lit" onClick={() => void load()}>
                Check again
              </Door>
              <Door tone="dark" href="/watch">
                Watch a recorded run
              </Door>
            </Gate>
          }
        >
          <p>This run’s report lives on the live service, so it can’t be loaded right now.</p>
          {state.message ? <p>{state.message}</p> : null}
        </RunNotice>
      ) : null}
    </>
  );
}
