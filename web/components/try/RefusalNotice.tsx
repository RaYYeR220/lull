"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import type { RefusalCode } from "@/lib/api";
import { cx } from "@/lib/cx";
import type { Recorded } from "@/lib/recorded";
import { watchHref } from "@/lib/routes";
import { clock } from "@/lib/run-format";
import { isoDuration } from "@/lib/timecode";
import shared from "./shared.module.css";
import styles from "./RefusalNotice.module.css";

/** The API's refusals, plus the ways a run can end badly once it has started. */
export type NoticeKind = RefusalCode | "run_timeout" | "failed" | "lost";

const BUDGET: Record<string, string> = {
  total: "The live demo’s model budget is spent.",
  day: "Today’s model budget for live runs is spent.",
  run: "This clip would cost more than one run may spend.",
  provider: "The model provider isn’t taking new spend right now.",
};

const HEADINGS: Record<NoticeKind, string> = {
  budget: BUDGET.total!,
  busy: "Another description is running.",
  too_large: "This file is too large.",
  format: "Lull can’t take this file type.",
  unsupported: "Lull can’t take this video as it is.",
  too_long: "This video is too long.",
  unreadable: "This file couldn’t be read as a video.",
  rate_limited: "You’ve reached the live limit for now.",
  uploads_busy: "Other uploads are being checked.",
  storage: "The video couldn’t be stored just now.",
  one_upload_at_a_time: "Your last upload is still arriving.",
  timeout: "The upload took too long to arrive.",
  invalid: "The live service didn’t accept the request.",
  sample: "That sample isn’t available.",
  disconnected: "The upload was cut off.",
  not_found: "There’s no run with that id.",
  forbidden: "This browser can’t change this run.",
  read_only: "Recorded runs are read-only.",
  not_ready: "This run isn’t published yet.",
  not_editable: "This run can’t be edited.",
  lint: "The line breaks a writing rule.",
  invalid_transition: "This line can’t be approved as it is.",
  overlap: "Nothing was published.",
  not_voiced: "Nothing was published.",
  overloaded: "The studio is busy.",
  engine: "The edit couldn’t be checked.",
  publish_failed: "Publishing failed.",
  fork_failed: "The run couldn’t be copied.",
  unreachable: "The live service isn’t answering.",
  unavailable: "The live service is unavailable right now.",
  server: "The live service couldn’t finish the request.",
  run_timeout: "The run took too long and was stopped.",
  failed: "The run stopped.",
  lost: "The connection to this run was lost.",
};

/** The heading for a notice: plain words for what happened. */
export function noticeHeading(kind: NoticeKind, scope?: string | null, stageName?: string | null): string {
  if (kind === "budget") return (scope && BUDGET[scope]) || HEADINGS.budget;
  if (kind === "failed" && stageName) return `The run stopped at ${stageName}.`;
  return HEADINGS[kind];
}

function waitWords(s: number): string {
  if (s >= 3600) {
    const h = Math.ceil(s / 3600);
    return `about ${h} ${h === 1 ? "hour" : "hours"}`;
  }
  return clock(s);
}

/** Counts down to zero on its own clock, so the page around it doesn't re-render each second. */
function Countdown({ seconds, onDone }: { seconds: number; onDone: () => void }) {
  const [left, setLeft] = useState(seconds);
  const done = useRef(onDone);
  useEffect(() => {
    done.current = onDone;
  });
  useEffect(() => {
    const end = Date.now() + seconds * 1000;
    const id = window.setInterval(() => {
      const l = Math.max(0, Math.ceil((end - Date.now()) / 1000));
      setLeft(l);
      if (l === 0) {
        window.clearInterval(id);
        done.current();
      }
    }, 250);
    return () => window.clearInterval(id);
  }, [seconds]);
  return <>{left > 0 ? `You can try again in ${waitWords(left)}.` : "You can try again now."}</>;
}

type Props = {
  kind: NoticeKind;
  scope?: string | null;
  /** The stage a failed run stopped in, by name. */
  stageName?: string | null;
  /** What happened and what to do, in plain words (usually the API's own message). */
  message: string;
  /** Seconds to wait before trying again (Retry-After). */
  retryAfter?: number | null;
  /** When set, a button tries again (after the wait). */
  onRetry?: () => void;
  retryLabel?: string;
  /** Said once the wait is over. */
  onReady?: () => void;
  recorded: readonly Recorded[];
  /** Move focus to the heading when shown (it replaced the control that was pressed). */
  focus?: boolean;
  /** 3 inside the run panel, under its own heading. */
  level?: 2 | 3;
  className?: string;
};

/** A refusal or a stopped run, in plain words, with the wait, the way to try again and the recorded runs. */
export function RefusalNotice({
  kind,
  scope,
  stageName,
  message,
  retryAfter,
  onRetry,
  retryLabel = "Try again",
  onReady,
  recorded,
  focus = false,
  level = 2,
  className,
}: Props) {
  const H = level === 3 ? "h3" : "h2";
  const id = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const waits = retryAfter !== null && retryAfter !== undefined && retryAfter > 0;
  const [ready, setReady] = useState(!waits);

  useEffect(() => {
    if (focus) heading.current?.focus();
  }, [focus]);

  return (
    <section aria-labelledby={`${id}-h`} className={cx(styles.notice, className)} data-notice={kind}>
      <H id={`${id}-h`} ref={heading} tabIndex={-1} className={styles.h}>
        {noticeHeading(kind, scope, stageName)}
      </H>
      <p className={styles.body}>{message}</p>

      {onRetry ? (
        <div className={styles.retry}>
          <button type="button" className={shared.btn} onClick={onRetry} disabled={!ready} aria-describedby={waits ? `${id}-wait` : undefined}>
            {retryLabel}
          </button>
          {waits ? (
            <p id={`${id}-wait`} className={styles.wait}>
              <Countdown
                seconds={retryAfter}
                onDone={() => {
                  setReady(true);
                  onReady?.();
                }}
              />
            </p>
          ) : null}
        </div>
      ) : null}

      {recorded.length ? (
        <div className={styles.rec}>
          <p className={styles.recLead}>Recorded runs play without the live service:</p>
          <ul>
            {recorded.map((r) => (
              <li key={r.id}>
                <Link href={watchHref(r.id)} prefetch={false} className={shared.link}>
                  {r.title}
                </Link>
                <time className={cx(shared.mono, styles.dur)} dateTime={isoDuration(r.duration)}>
                  {clock(r.duration)}
                </time>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
