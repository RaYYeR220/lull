"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Door, Gate } from "@/components/ui/Gate";
import { MetaList } from "@/components/ui/MetaList";
import { ForkDoor } from "@/components/watch/ForkDoor";
import { RunChecks } from "@/components/watch/RunChecks";
import { RunFiles } from "@/components/watch/RunFiles";
import { WatchRun } from "@/components/watch/WatchRun";
import { ApiError, isAbort, readEditToken } from "@/lib/api";
import { cx } from "@/lib/cx";
import { followHref, studioHref } from "@/lib/routes";
import { loadRun, type Loaded } from "@/lib/run-load";
import { voicedLines, type RunData } from "@/lib/run-data";
import type { ShowcaseIndexEntry } from "@/lib/showcase-schema.mjs";
import { creditParts } from "@/lib/showcase-derive";
import { formatTimecode } from "@/lib/timecode";
import page from "@/app/watch/watch.module.css";
import run from "@/components/watch/Watch.module.css";
import styles from "./LiveRun.module.css";

type State = { kind: "loading" } | { kind: "down"; message: string | null } | Loaded;

/** "28 October 2026, 21:45" in the visitor's own time. */
function when(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString("en-GB", { day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** A run that isn't there to watch: a heading, plain words, and the ways on. */
function Notice({ title, children, doors }: { title: string; children: ReactNode; doors: ReactNode }) {
  return (
    <section className={styles.notice} aria-labelledby="run-state" data-run-notice="">
      <Eyebrow className={page.eyebrow}>Live run</Eyebrow>
      <h1 id="run-state" className={cx("t-display-2", styles.noticeTitle)} tabIndex={-1}>
        {title}
      </h1>
      <div className={styles.noticeBody}>{children}</div>
      <div className={styles.noticeDoors}>{doors}</div>
    </section>
  );
}

function Head({ data }: { data: RunData }) {
  const voiced = voicedLines(data).length;
  const extended = data.cues.filter((c) => c.kind === "extended").length;
  const credit = data.credit ? creditParts(data.credit) : null;
  const made = when(data.created);
  return (
    <header className={page.runHead}>
      <Eyebrow className={page.eyebrow}>Live run, finished</Eyebrow>
      <h1 className={cx("t-display-2", page.runTitle)}>{data.title}</h1>
      <p className={cx("t-lede", page.runLede)}>
        Described by a live run{made ? `, published ${made}` : ""}. It plays from the files the run produced; nothing is sent to a model while
        you watch.
      </p>
      {data.dirty ? (
        <p className={styles.dirty}>Lines were changed in the studio after this was published. What plays here is the published version.</p>
      ) : null}
      {credit ? (
        <p className={page.credit}>
          {credit.before}
          {credit.licence ? (
            <a href={credit.licence.url} rel="license noopener" target="_blank">
              {credit.licence.label}
              <span className="sr-only"> (opens in a new tab)</span>
            </a>
          ) : null}
          {credit.after}
        </p>
      ) : null}
      <MetaList
        label="This run"
        className={page.meta}
        items={[
          <span key="len">
            <b>{formatTimecode(data.duration, 0)}</b> long
          </span>,
          <span key="lines">
            <b>{voiced}</b> {voiced === 1 ? "line" : "lines"} in its pauses
          </span>,
          ...(extended
            ? [
                <span key="ext">
                  <b>{extended}</b> extended
                </span>,
              ]
            : []),
          ...(data.file && data.file !== data.title
            ? [
                <span key="file" className={styles.mono}>
                  {data.file}
                </span>,
              ]
            : []),
        ]}
      />
    </header>
  );
}

function EditBlock({ data, owner }: { data: RunData; owner: boolean }) {
  return (
    <section aria-labelledby="edit-title" className={run.editBlock}>
      <h2 id="edit-title" className={run.h2}>
        Change a line
      </h2>
      {owner ? (
        <>
          <p className={run.editNote}>This browser holds the edit key for this run. Review each line with its evidence, edit, approve, then publish again.</p>
          <div className={run.fork}>
            <Gate label="Edit this run" className={run.doors}>
              <Door tone="lit" href={studioHref(data.id)} className={run.forkDoor}>
                Review in the studio
              </Door>
            </Gate>
          </div>
        </>
      ) : (
        <>
          <p className={run.editNote}>
            Only the browser that started this run can change it. Open it in the studio to see each line with its evidence, or make your own copy to
            edit.
          </p>
          <ForkDoor runId={data.id}>
            <Door tone="dark" href={studioHref(data.id)} className={run.studioDoor}>
              Open in Studio
            </Door>
          </ForkDoor>
        </>
      )}
    </section>
  );
}

/**
 * A finished live run, loaded from the API: the same player, script, checks and files as a
 * recorded run's page. Until it is published the page says where it stands: still being
 * described (with the way to follow it), stopped (with why), or not there.
 */
export function LiveRun({ id, index }: { id: string; index: ShowcaseIndexEntry[] }) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [owner, setOwner] = useState(false);
  const ctl = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    ctl.current?.abort();
    const c = new AbortController();
    ctl.current = c;
    setState({ kind: "loading" });
    try {
      const got = await loadRun(id, { index, signal: c.signal });
      if (!c.signal.aborted) setState(got);
    } catch (e) {
      if (isAbort(e) || c.signal.aborted) return;
      // the API's own words when it answered with a refusal; nothing more to say when it didn't answer
      setState({ kind: "down", message: e instanceof ApiError && e.code !== "unreachable" ? e.message : null });
    }
  }, [id, index]);

  useEffect(() => {
    setOwner(!!readEditToken(id));
    void load();
    return () => ctl.current?.abort();
  }, [id, load]);

  useEffect(() => {
    if (state.kind === "published") document.title = `${state.run.title}, described | Lull`;
  }, [state]);

  const status =
    state.kind === "loading" ? "Loading the run…" : state.kind === "published" ? `${state.run.title} is ready to play.` : "";

  return (
    <>
      <p role="status" className={state.kind === "loading" ? styles.loading : "sr-only"}>
        {status}
      </p>
      {state.kind === "published" ? (
        <WatchRun
          media={state.run.media}
          label={`${state.run.title}, described`}
          cues={state.run.cues}
          head={<Head data={state.run} />}
          edit={<EditBlock data={state.run} owner={owner} />}
          files={<RunFiles files={state.run.files} note="Served by the live service." />}
          checks={<RunChecks run={state.run} />}
        />
      ) : state.kind === "running" ? (
        <Notice
          title="This run is still being described."
          doors={
            <Gate label="Follow the run">
              <Door tone="lit" href={followHref(id)}>
                Follow it live
              </Door>
              <Door tone="dark" href="/watch">
                Watch a recorded run
              </Door>
            </Gate>
          }
        >
          <p>Its stages, model calls and lines show up as they happen on the try-it page. Once it is published, this page plays it.</p>
        </Notice>
      ) : state.kind === "failed" ? (
        <Notice
          title="This run stopped before it finished."
          doors={
            <Gate label="What next">
              <Door tone="lit" href="/try">
                Describe a clip
              </Door>
              <Door tone="dark" href="/watch">
                Watch a recorded run
              </Door>
            </Gate>
          }
        >
          {state.reason ? <p className={styles.reason}>{state.reason}</p> : <p>The run didn’t record why it stopped.</p>}
          <p>Nothing was published from it, so there is nothing to play.</p>
        </Notice>
      ) : state.kind === "missing" ? (
        <Notice
          title="There’s no run with that id."
          doors={
            <Gate label="What next">
              <Door tone="lit" href="/try">
                Describe a clip
              </Door>
              <Door tone="dark" href="/watch">
                Watch a recorded run
              </Door>
            </Gate>
          }
        >
          <p>It may have been removed: copies made for editing are kept for a day. Check the address, or start a new run.</p>
        </Notice>
      ) : state.kind === "down" ? (
        <Notice
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
          <p>This run lives on the live service, so it can’t be loaded right now. Recorded runs play from this site and still work.</p>
          {state.message ? <p className={styles.reason}>{state.message}</p> : null}
        </Notice>
      ) : null}
    </>
  );
}
