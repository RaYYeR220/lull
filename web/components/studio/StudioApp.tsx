"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Player, type PlayerElement } from "@/components/player/Player";
import { RunNotice } from "@/components/run/RunNotice";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Door, Gate } from "@/components/ui/Gate";
import { Glyph } from "@/components/ui/Glyph";
import { MetaList } from "@/components/ui/MetaList";
import { STATUS } from "@/components/ui/StatusMark";
import { ForkDoor } from "@/components/watch/ForkDoor";
import { API_REFUSALS, ApiError, FORK_TTL_MS, isAbort, readEditToken, readForkRecord, type CueStatus, type ForkRecord } from "@/lib/api";
import { approveCue, editCue, getRun, holdCue, publishRun, type RunDetail } from "@/lib/api-runs";
import { cx } from "@/lib/cx";
import { followHref, reportHref, runHref, watchHref } from "@/lib/routes";
import { buildRun, loadRun } from "@/lib/run-load";
import type { RunData } from "@/lib/run-data";
import type { Showcase, ShowcaseIndexEntry } from "@/lib/showcase-schema.mjs";
import { formatTimecode } from "@/lib/timecode";
import { CueDrawer, type Busy, type Outcome } from "./CueDrawer";
import { CueList } from "./CueList";
import { RunSummary } from "./RunSummary";
import { Timeline } from "./Timeline";
import page from "@/app/watch/watch.module.css";
import styles from "./Studio.module.css";

type Load =
  | { kind: "loading" }
  | { kind: "ready" }
  | { kind: "running" }
  | { kind: "failed"; reason: string | null }
  | { kind: "missing" }
  | { kind: "down"; message: string | null };

type Publish = { kind: "idle" } | { kind: "publishing" } | { kind: "done" } | { kind: "refused"; message: string; cueIds: string[] };

const ORDER: CueStatus[] = ["verified", "edited", "approved", "extended", "held"];

/** "28 Oct, 21:40" in the visitor's time. */
const shortTime = (ms: number) => new Date(ms).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

/** The line named in the address (?cue=c003), when there is one. */
function cueInAddress(): string | null {
  try {
    return new URL(window.location.href).searchParams.get("cue");
  } catch {
    return null;
  }
}

function setCueInAddress(id: string) {
  try {
    const url = new URL(window.location.href);
    url.searchParams.set("cue", id);
    window.history.replaceState(window.history.state, "", url);
  } catch {
    // an address the browser won't rewrite: the selection still works
  }
}

type Props = {
  id: string;
  /** A recorded run's data, rendered on the server; null for a run that lives on the API. */
  initial: RunData | null;
  /** The recorded run itself (for its soundtrack and files when the API adds its checks). */
  clip: Showcase | null;
  index: readonly ShowcaseIndexEntry[];
};

/**
 * The studio: a run's lines on a timeline with the player, one line open in the drawer, the
 * run's summary above. A person reviews each line against its evidence and edits, approves or
 * holds it; the API checks an edit again (writing rules, the frames, the voice) and publishing
 * goes through the overlap gate. Recorded runs, and runs this browser has no edit key for, open
 * read-only, with the way to make an editable copy.
 */
export function StudioApp({ id, initial, clip, index }: Props) {
  const recorded = initial !== null;
  const [load, setLoad] = useState<Load>(recorded ? { kind: "ready" } : { kind: "loading" });
  const [run, setRun] = useState<RunData | null>(initial);
  /** For a recorded run: whether the API's checks (claims, findings, notes, summary) came in. */
  const [api, setApi] = useState<"pending" | "ok" | "down">(recorded ? "pending" : "ok");
  const [token, setToken] = useState<string | null>(null);
  const [fork, setFork] = useState<ForkRecord | null>(null);
  const [selected, setSelected] = useState<string | null>(initial?.cues[0]?.id ?? null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<{ id: string; action: Busy } | null>(null);
  const [outcomes, setOutcomes] = useState<Record<string, Outcome>>({});
  const [publish, setPublish] = useState<Publish>({ kind: "idle" });
  const versions = useRef({ audio: 0, files: 0 });
  const player = useRef<PlayerElement>(null);
  const drawerHeading = useRef<HTMLHeadingElement>(null);
  const drawerBox = useRef<HTMLDivElement>(null);
  const ctl = useRef<AbortController | null>(null);

  const editable = !recorded && !!token && load.kind === "ready";
  const publishing = publish.kind === "publishing";
  const cue = run?.cues.find((c) => c.id === selected) ?? null;

  const build = useCallback(
    (detail: RunDetail, signal?: AbortSignal) =>
      buildRun(id, detail, { index, clip, clipFiles: initial?.files, audioVersion: versions.current.audio, filesVersion: versions.current.files, signal }),
    [id, index, clip, initial],
  );

  const fetchRun = useCallback(async () => {
    ctl.current?.abort();
    const c = new AbortController();
    ctl.current = c;
    if (recorded) {
      // the recorded files are already here; the API adds each line's checks and the summary
      try {
        const next = await build(await getRun(id, c.signal), c.signal);
        if (!c.signal.aborted) {
          setRun(next);
          setApi("ok");
        }
      } catch (e) {
        if (!isAbort(e) && !c.signal.aborted) setApi("down");
      }
      return;
    }
    setLoad({ kind: "loading" });
    try {
      const got = await loadRun(id, { index, signal: c.signal });
      if (c.signal.aborted) return;
      if (got.kind === "published") {
        setRun(got.run);
        setSelected((s) => s ?? (got.run.cues.find((x) => x.id === cueInAddress())?.id ?? got.run.cues[0]?.id ?? null));
        setLoad({ kind: "ready" });
      } else setLoad(got);
    } catch (e) {
      if (isAbort(e) || c.signal.aborted) return;
      setLoad({ kind: "down", message: e instanceof ApiError && e.code !== "unreachable" ? e.message : null });
    }
  }, [id, index, recorded, build]);

  useEffect(() => {
    setToken(readEditToken(id));
    setFork(readForkRecord(id));
    const named = cueInAddress();
    if (named && initial?.cues.some((c) => c.id === named)) setSelected(named);
    void fetchRun();
    return () => ctl.current?.abort();
  }, [id, initial, fetchRun]);

  /**
   * The run as the API has it now (after a change: its cues, takes and dirty flag). False when
   * it can't be read back: the change went through, but the page would only show stale data.
   */
  const refresh = useCallback(async (): Promise<boolean> => {
    try {
      setRun(await build(await getRun(id)));
      return true;
    } catch {
      return false;
    }
  }, [id, build]);

  const choose = useCallback(
    (cueId: string, how: "timeline" | "list" = "timeline") => {
      setSelected(cueId);
      setCueInAddress(cueId);
      const c = run?.cues.find((x) => x.id === cueId);
      if (c) player.current?.seek?.(c.start);
      // on one column the drawer sits under the timeline: bring it into view
      if (how === "list" && drawerBox.current && window.matchMedia("(max-width: 1099px)").matches) {
        const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        drawerBox.current.scrollIntoView({ block: "start", behavior: still ? "auto" : "smooth" });
      }
    },
    [run],
  );
  const chooseFromList = useCallback((cueId: string) => choose(cueId, "list"), [choose]);
  const openDrawer = useCallback(() => drawerHeading.current?.focus(), []);

  const say = (cueId: string, o: Outcome) => setOutcomes((all) => ({ ...all, [cueId]: o }));
  const refused = (e: unknown): Outcome => {
    if (!(e instanceof ApiError)) return { tone: "refused", text: "The change couldn’t be made. Try again." };
    // the API's own refusals carry their words; anything else is the server's, not a guess
    const own = e.apiCode !== null && API_REFUSALS.has(e.apiCode);
    if (!own && e.status >= 400 && e.status < 500)
      return { tone: "refused", text: `The server refused this change: ${(e.said ?? `it answered ${e.status}`).replace(/[.\s]+$/, "")}.` };
    return { tone: "refused", text: e.message, findings: e.detail.findings, checked: e.detail.text };
  };
  /** Said when a change went through but the run couldn't be read back. */
  const STALE: Outcome = { tone: "held", text: "Saved, but the page couldn’t reload the run. Reload to see its current state." };

  /** One change at a time, even from a double press (state alone updates too late for that). */
  const inFlight = useRef(false);
  const change = async (cueId: string, action: Exclude<Busy, null>, work: () => Promise<Outcome | null>) => {
    if (inFlight.current || !token) return;
    inFlight.current = true;
    setBusy({ id: cueId, action });
    setPublish({ kind: "idle" });
    try {
      const said = await work();
      if (said) say(cueId, said);
    } catch (e) {
      say(cueId, refused(e));
    } finally {
      inFlight.current = false;
      setBusy(null);
    }
  };

  const save = () => {
    if (!cue || !token || busy || publishing) return;
    const c = cue;
    const text = drafts[c.id] ?? c.text;
    return change(c.id, "edit", async () => {
      const done = await editCue(id, c.id, text, token);
      versions.current.audio += 1;
      if (!(await refresh())) return STALE; // the draft stays until the run is read back
      // the draft goes only when it is still the text that was saved
      setDrafts((d) => {
        if (d[c.id] !== text) return d;
        const next = { ...d };
        delete next[c.id];
        return next;
      });
      return done.status === "held"
        ? { tone: "held", text: `${c.label} is held: ${done.note ?? "it didn’t pass the check"}. Edit it again, or leave it held.` }
        : {
            tone: "good",
            text: `${c.label} is edited: it passed the frame check and was voiced again.${done.note ? ` ${done.note.charAt(0).toUpperCase()}${done.note.slice(1)}.` : ""}`,
          };
    });
  };

  const approve = () => {
    if (!cue || !token || busy || publishing) return;
    const c = cue;
    return change(c.id, "approve", async () => {
      await approveCue(id, c.id, token);
      if (!(await refresh())) return STALE;
      return { tone: "good", text: `${c.label} is approved.` };
    });
  };

  const hold = (reason: string) => {
    if (!cue || !token || busy || publishing) return;
    const c = cue;
    return change(c.id, "hold", async () => {
      const done = await holdCue(id, c.id, token, reason);
      if (!(await refresh())) return STALE;
      return { tone: "held", text: `${c.label} is held${done.note ? `: ${done.note}` : ""}. It won’t be voiced until it is edited.` };
    });
  };

  const doPublish = async () => {
    if (!token || busy || inFlight.current || publish.kind === "publishing" || !run?.dirty) return;
    inFlight.current = true;
    setPublish({ kind: "publishing" });
    try {
      const detail = await publishRun(id, token);
      versions.current.files += 1;
      setRun(await build(detail));
      setPublish({ kind: "done" });
    } catch (e) {
      setPublish({ kind: "refused", message: refused(e).text, cueIds: e instanceof ApiError ? (e.detail.cueIds ?? []) : [] });
    } finally {
      inFlight.current = false;
    }
  };

  const blocked = useMemo(() => new Set(publish.kind === "refused" ? publish.cueIds : []), [publish]);

  // -- states without a run to show
  if (load.kind !== "ready" || !run) {
    const recordedDoor = (
      <Gate label="What next">
        <Door tone="lit" href="/try">
          Describe a clip
        </Door>
        <Door tone="dark" href="/watch">
          Open a recorded run
        </Door>
      </Gate>
    );
    return (
      <>
        <p role="status" className={load.kind === "loading" ? styles.loading : "sr-only"}>
          {load.kind === "loading" ? "Loading the run…" : ""}
        </p>
        {load.kind === "running" ? (
          <RunNotice
            eyebrow="Studio"
            title="This run is still being described."
            doors={
              <Gate label="Follow the run">
                <Door tone="lit" href={followHref(id)}>
                  Follow it live
                </Door>
                <Door tone="dark" href="/watch">
                  Open a recorded run
                </Door>
              </Gate>
            }
          >
            <p>Its lines can be reviewed here once it is published.</p>
          </RunNotice>
        ) : load.kind === "failed" ? (
          <RunNotice eyebrow="Studio" title="This run stopped before it finished." doors={recordedDoor}>
            {load.reason ? <p className={styles.quoted}>{load.reason}</p> : null}
            <p>Nothing was published from it, so there are no lines to review.</p>
          </RunNotice>
        ) : load.kind === "missing" ? (
          <RunNotice eyebrow="Studio" title="There’s no run with that id." doors={recordedDoor}>
            <p>It may have been removed: copies made for editing are kept for a day. Check the address, or make a new copy from a recorded run.</p>
          </RunNotice>
        ) : load.kind === "down" ? (
          <RunNotice
            eyebrow="Studio"
            title="The live service isn’t answering."
            doors={
              <Gate label="What next">
                <Door tone="lit" onClick={() => void fetchRun()}>
                  Check again
                </Door>
                <Door tone="dark" href="/watch">
                  Open a recorded run
                </Door>
              </Gate>
            }
          >
            <p>This run lives on the live service, so it can’t be opened right now. Recorded runs still open in the studio.</p>
            {load.message ? <p className={styles.quoted}>{load.message}</p> : null}
          </RunNotice>
        ) : null}
      </>
    );
  }

  const counts = ORDER.map((s) => [s, run.cues.filter((c) => c.status === s).length] as const).filter(([, n]) => n > 0);
  const voiced = run.cues.filter((c) => c.status !== "held").length;
  const dirty = run.dirty === true;
  const publishReason = !dirty ? "Nothing to publish: no change since the last publish." : busy ? "Wait for the change in progress to finish." : null;

  const readOnly = recorded ? (
    <>
      <p className={styles.roNote}>
        A recorded run stays as it was made. Make your own copy to edit its lines, have them checked again and publish your version.
      </p>
      <ForkDoor runId={id} label="Edit a copy" />
    </>
  ) : (
    <>
      <p className={styles.roNote}>
        This browser doesn’t hold the edit key for this run, so its lines can’t be changed here. Only the browser that started the run, or made
        the copy, can edit it. You can make a copy of your own.
      </p>
      <ForkDoor runId={id} label="Edit a copy" />
    </>
  );

  return (
    <div className={styles.studio}>
      <header className={styles.top}>
        <div className={styles.titleBlock}>
          <Eyebrow className={page.eyebrow}>{recorded ? "Studio, recorded run" : editable ? "Studio, your run" : "Studio"}</Eyebrow>
          <h1 className={cx("t-display-2", styles.h1)}>{run.title}</h1>
          <p className={styles.mode}>
            {recorded
              ? "Read-only: every line with its evidence, as the run made it."
              : editable
                ? "Edit, approve or hold each line, then publish. Every edit is checked against the frames and voiced again."
                : "Read-only in this browser."}
            {editable && fork ? ` This copy is kept until ${shortTime(fork.made + FORK_TTL_MS)}, a day after it was made.` : ""}
          </p>
          <MetaList
            label="This run"
            className={styles.meta}
            items={[
              <span key="len">
                <b>{formatTimecode(run.duration, 0)}</b> long
              </span>,
              <span key="lines">
                <b>{run.cues.length}</b> {run.cues.length === 1 ? "line" : "lines"}
              </span>,
              ...(run.voice
                ? [
                    <span key="voice">
                      voice <b>{run.voice}</b>
                      {run.wps ? `, about ${run.wps.toFixed(1)} words a second` : ""}
                    </span>,
                  ]
                : run.wps
                  ? [<span key="wps">about {run.wps.toFixed(1)} words a second</span>]
                  : []),
            ]}
          />
          <p className={styles.links}>
            <a href={recorded ? watchHref(id) : runHref(id)} className={styles.metaLink}>
              Watch it described
            </a>
            <a href={reportHref(id, recorded ? "recorded" : "live")} className={styles.metaLink}>
              Conformance report
            </a>
          </p>
        </div>
        <RunSummary run={run} />
      </header>

      {recorded && api === "down" ? (
        <p className={styles.notice}>
          The live service didn’t answer for this run, so each line’s claims, writing checks and notes aren’t shown. The timeline, lines and evidence
          frames are from the recorded files.
        </p>
      ) : null}

      <div className={styles.desk}>
        <div className={styles.bar}>
          <span className={styles.brand}>
            <span className={styles.wm}>lull</span>
            <span>Studio</span>
          </span>
          <ul className={styles.counts} aria-label="Lines by status">
            {counts.map(([s, n]) => (
              <li key={s} className={styles[`c_${s}`]}>
                <Glyph name={STATUS[s].glyph} size={11} />
                {n} {STATUS[s].word.toLowerCase()}
              </li>
            ))}
          </ul>
          {editable ? (
            <div className={styles.publish}>
              {dirty ? <span className={styles.dirty}>Changed since the last publish</span> : null}
              <button
                type="button"
                className={cx(styles.publishBtn, dirty && !busy && styles.publishLive)}
                aria-disabled={!!publishReason || publish.kind === "publishing" || undefined}
                aria-describedby={publishReason ? "publish-why" : undefined}
                onClick={() => void doPublish()}
              >
                {publish.kind === "publishing" ? "Publishing…" : "Publish"}
              </button>
              {publishReason ? (
                <span id="publish-why" className="sr-only">
                  {publishReason}
                </span>
              ) : null}
            </div>
          ) : (
            <span className={styles.roTag}>{recorded ? "Recorded run, read-only" : "Read-only"}</span>
          )}
        </div>
        <div role="status" className={cx(styles.publishOutcome, publish.kind === "refused" && styles.publishRefused, publish.kind === "done" && styles.publishDone)}>
          {publish.kind === "done"
            ? `Published: the files now carry ${voiced} ${voiced === 1 ? "line" : "lines"}, and coverage and WCAG are worked out again.`
            : publish.kind === "refused"
              ? publish.message
              : ""}
        </div>
        {publish.kind === "refused" && publish.cueIds.length ? (
          <div className={styles.gate}>
            <p>Lines that stopped it:</p>
            <ul>
              {publish.cueIds.map((cid) => {
                const c = run.cues.find((x) => x.id === cid);
                return (
                  <li key={cid}>
                    <button type="button" className={styles.textBtn} onClick={() => choose(cid, "list")}>
                      {c ? `${c.label} at ${formatTimecode(c.start)}` : cid}
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        ) : null}

        <div className={styles.body}>
          <div className={styles.work}>
            <Timeline run={run} selected={selected} onSelect={choose} onOpen={openDrawer} player={player} blocked={blocked} />
            <div className={styles.screen}>
              {run.media ? (
                <Player
                  ref={player}
                  src={run.media.src}
                  descriptions={run.media.descriptions}
                  extended={run.media.extended}
                  describedAudio={run.media.describedAudio}
                  cueAudioBase={run.media.cueAudioBase}
                  cueAudioExt={run.media.cueAudioExt}
                  crossOrigin={run.media.crossOrigin}
                  sound={run.media.sound}
                  label={`${run.title}, described`}
                />
              ) : (
                <p className={styles.noMedia}>This run’s files don’t include a video to play.</p>
              )}
              {dirty ? <p className={styles.playNote}>The player plays the last published version. Publish to hear your changes in it.</p> : null}
            </div>
          </div>
          <aside className={styles.side} ref={drawerBox} aria-labelledby="drawer-title">
            {cue ? (
              <CueDrawer
                run={run}
                cue={cue}
                editable={editable}
                readOnly={readOnly}
                draft={drafts[cue.id] ?? cue.text}
                onDraft={(t) => setDrafts((d) => ({ ...d, [cue.id]: t }))}
                onSave={() => void save()}
                onApprove={() => void approve()}
                onHold={(r) => void hold(r)}
                borrowedGaps={!!run.borrowed?.gaps}
                busy={busy?.id === cue.id ? busy.action : null}
                locked={(busy !== null && busy.id !== cue.id) || publishing}
                outcome={outcomes[cue.id] ?? null}
                headingRef={drawerHeading}
              />
            ) : (
              <p className={styles.empty} id="drawer-title">
                This run has no lines.
              </p>
            )}
          </aside>
          <div className={styles.listArea}>
            <CueList cues={run.cues} selected={selected} onChoose={chooseFromList} borrowedGaps={!!run.borrowed?.gaps} />
          </div>
        </div>
      </div>
    </div>
  );
}
