"use client";

import { useEffect, useId, useState, type ReactNode, type RefObject } from "react";
import { Glyph } from "@/components/ui/Glyph";
import { StatusMark } from "@/components/ui/StatusMark";
import { EvidenceFrame } from "@/components/watch/CueScript";
import type { LintFinding } from "@/lib/api";
import { cx } from "@/lib/cx";
import type { RunCue, RunData } from "@/lib/run-data";
import { modelName, usd } from "@/lib/run-format";
import { budget } from "@/lib/studio-budget";
import { formatTimecode } from "@/lib/timecode";
import { BudgetMeter } from "./BudgetMeter";
import styles from "./CueDrawer.module.css";

export type Busy = "edit" | "approve" | "hold" | null;

/** What the last action on this line came to, in plain words. */
export type Outcome = {
  tone: "good" | "held" | "refused";
  text: string;
  /** A refused edit's writing findings, and the text as the API checked it. */
  findings?: LintFinding[];
  checked?: string;
};

const VERDICT: Record<string, { word: string; ok: boolean }> = {
  supported: { word: "Seen", ok: true },
  not_visible: { word: "Not visible, cut", ok: false },
  contradicted: { word: "Contradicted, cut", ok: false },
};

/** Why a line can't be approved as it is, or null when it can. */
export function approveBlock(c: RunCue): string | null {
  if (c.status === "approved") return "This line is approved.";
  if (c.status === "held") return "A held line can’t be approved: edit it so it is checked again.";
  if (c.audioDur === null) return "This line has no recording: edit it so it is voiced again.";
  return null;
}

function where(c: RunCue): string {
  if (c.kind === "extended") {
    const hold = c.end !== null ? `, for ${(c.end - c.start).toFixed(2)} s` : "";
    return `The video holds at ${formatTimecode(c.start)}${hold} while this line is said.`;
  }
  if (c.gap) return `In the pause from ${formatTimecode(c.gap.start)} to ${formatTimecode(c.gap.end)}, ${(c.gap.end - c.gap.start).toFixed(2)} s.`;
  return `Starts at ${formatTimecode(c.start)}.`;
}

function Provenance({ run, cue }: { run: RunData; cue: RunCue }) {
  const p = run.provenance;
  if (!p) return null;
  const chips: string[] = [];
  if (p.watch) chips.push(`Watched by ${modelName(p.watch)}`);
  if (cue.status === "edited") chips.push("Written by a person");
  else if (p.plan) chips.push(`Written by ${modelName(p.plan)}`);
  if (p.verify) chips.push(`Checked by ${modelName(p.verify)}`);
  if (cue.status === "approved") chips.push("Approved by a person");
  if (p.voice && cue.audioDur !== null && cue.status !== "held") chips.push(`Voiced by ${p.voice}`);
  if (!chips.length) return null;
  return (
    <ul className={styles.chips} aria-label="Who made this line">
      {chips.map((c) => (
        <li key={c}>{c}</li>
      ))}
    </ul>
  );
}

type Props = {
  run: RunData;
  cue: RunCue;
  editable: boolean;
  /** In place of the actions when the line can't be changed here. */
  readOnly: ReactNode;
  draft: string;
  onDraft: (text: string) => void;
  onSave: () => void;
  onApprove: () => void;
  onHold: (reason: string) => void;
  busy: Busy;
  /** Another line's change is in progress: actions wait for it. */
  locked?: boolean;
  outcome: Outcome | null;
  headingRef: RefObject<HTMLHeadingElement | null>;
};

/**
 * One line, in full: where it sits, its evidence frame, the words against its pause, what the
 * frames said about each claim, the writing checks, who made it, and the actions: edit (the
 * API checks and voices it again), approve, hold.
 */
export function CueDrawer({ run, cue, editable, readOnly, draft, onDraft, onSave, onApprove, onHold, busy: own, locked = false, outcome, headingRef }: Props) {
  // while another line is being changed, this one's actions wait as if busy
  const busy: Busy | "other" = own ?? (locked ? "other" : null);
  const ids = useId();
  const meterId = `${ids}-meter`;
  const [holding, setHolding] = useState(false);
  const [reason, setReason] = useState("");
  const [elapsed, setElapsed] = useState(0);

  // a new line closes the hold form
  useEffect(() => {
    setHolding(false);
    setReason("");
  }, [cue.id]);

  // how long an edit has been checking, said with the progress
  useEffect(() => {
    if (busy !== "edit") return;
    const t0 = Date.now();
    setElapsed(0);
    const id = window.setInterval(() => setElapsed(Math.floor((Date.now() - t0) / 1000)), 1000);
    return () => window.clearInterval(id);
  }, [busy]);

  const text = editable ? draft : cue.text;
  const b = budget(text, cue.kind === "standard" ? cue.gap : null, run.wps);
  const changed = editable && draft.trim() !== cue.text.trim();
  const empty = !draft.trim();
  const approve = approveBlock(cue);
  const findings = cue.findings ?? [];

  const progress =
    busy === "edit"
      ? `Checking the new line: writing rules, then the frames, then the voice.${elapsed >= 2 ? ` ${elapsed} s so far.` : ""}`
      : busy === "approve"
        ? "Approving…"
        : busy === "hold"
          ? "Holding the line…"
          : null;

  return (
    <div className={styles.drawer} data-drawer={cue.id}>
      <div className={styles.head}>
        <h2 id="drawer-title" ref={headingRef} tabIndex={-1} className={styles.title}>
          {cue.label}
        </h2>
        <StatusMark status={cue.status} variant="pill" />
      </div>
      <p className={styles.where}>{where(cue)}</p>

      <figure className={styles.frame}>
        <EvidenceFrame src={cue.frame} className={styles.img} empty="No frame was kept for this line." />
        <figcaption>
          <span>Evidence frame</span>
          <span className={styles.mono}>{formatTimecode(cue.start)}</span>
        </figcaption>
      </figure>

      <div className={styles.line}>
        {editable ? (
          <>
            <label htmlFor={`${ids}-text`} className={styles.label}>
              The line
            </label>
            <textarea
              id={`${ids}-text`}
              className={styles.text}
              value={draft}
              onChange={(e) => onDraft(e.target.value)}
              rows={3}
              maxLength={1000}
              spellCheck
              aria-describedby={meterId}
              aria-invalid={b.over || undefined}
            />
          </>
        ) : (
          <p className={styles.said}>{cue.text}</p>
        )}
        <BudgetMeter budget={b} id={meterId} className={styles.meter} />
        {b.over && editable ? (
          <p className={styles.warn}>Longer than its pause holds. The check may speed it up a little, or hold it if it still doesn’t fit.</p>
        ) : null}
      </div>

      {editable ? (
        <div className={styles.actions}>
          <div className={styles.row}>
            <button
              type="button"
              className={cx(styles.btn, styles.lit)}
              aria-disabled={!changed || empty || busy !== null || undefined}
              onClick={() => changed && !empty && !busy && onSave()}
            >
              {busy === "edit" ? "Checking…" : "Save and check again"}
            </button>
            {changed && !busy ? (
              <button type="button" className={styles.textBtn} onClick={() => onDraft(cue.text)}>
                Undo my changes
              </button>
            ) : null}
          </div>
          <div className={styles.row}>
            <button
              type="button"
              className={styles.btn}
              aria-disabled={!!approve || changed || busy !== null || undefined}
              aria-describedby={approve ? `${ids}-approve` : undefined}
              onClick={() => !approve && !changed && !busy && onApprove()}
            >
              <Glyph name="approved" size={11} />
              {busy === "approve" ? "Approving…" : "Approve"}
            </button>
            <button
              type="button"
              className={styles.btn}
              aria-expanded={holding}
              aria-controls={`${ids}-hold`}
              aria-disabled={busy !== null || undefined}
              onClick={() => !busy && setHolding((h) => !h)}
            >
              <Glyph name="held" size={11} />
              Hold…
            </button>
          </div>
          {approve ? (
            <p id={`${ids}-approve`} className={styles.why}>
              {approve}
            </p>
          ) : changed ? (
            <p className={styles.why}>Save your change first: approving signs off the line as it is now.</p>
          ) : null}
          {holding ? (
            <form
              id={`${ids}-hold`}
              className={styles.hold}
              onSubmit={(e) => {
                e.preventDefault();
                if (busy) return;
                onHold(reason);
                setHolding(false);
              }}
            >
              <label htmlFor={`${ids}-reason`} className={styles.label}>
                Why hold it? (optional)
              </label>
              <input id={`${ids}-reason`} className={styles.input} value={reason} maxLength={300} onChange={(e) => setReason(e.target.value)} />
              <div className={styles.row}>
                <button type="submit" className={styles.btn}>
                  Hold this line
                </button>
                <button type="button" className={styles.textBtn} onClick={() => setHolding(false)}>
                  Cancel
                </button>
              </div>
            </form>
          ) : null}
        </div>
      ) : (
        <div className={styles.readOnly}>{readOnly}</div>
      )}

      <div role="status" className={cx(styles.outcome, outcome && !progress && styles[outcome.tone])}>
        {progress ?? outcome?.text ?? ""}
      </div>
      {outcome?.findings?.length && !progress ? (
        <div className={styles.refusedDetail}>
          <ul className={styles.findings} aria-label="What the writing check found">
            {outcome.findings.map((f, i) => (
              <li key={i} data-severity={f.severity}>
                <span className={styles.rule}>{f.rule}</span>
                <span>{f.message}</span>
                <span className={styles.sev}>{f.severity === "error" ? "Must fix" : "Advice"}</span>
              </li>
            ))}
          </ul>
          {outcome.checked && outcome.checked.trim() !== draft.trim() ? (
            <p className={styles.checked}>
              Checked as <q>{outcome.checked}</q>
            </p>
          ) : null}
        </div>
      ) : null}

      {cue.note ? (
        <section className={cx(styles.block, cue.status === "held" && styles.noteHeld)} aria-labelledby={`${ids}-note`}>
          <h3 id={`${ids}-note`} className={styles.h3}>
            {cue.status === "held" ? "Why it is held" : "Note from the check"}
          </h3>
          <p className={styles.note}>{cue.note}</p>
        </section>
      ) : null}

      {cue.claims && cue.claims.length ? (
        <section className={styles.block} aria-labelledby={`${ids}-claims`}>
          <h3 id={`${ids}-claims`} className={styles.h3}>
            {cue.claimsOf === "draft" ? "Claims in the first draft, against the frames" : "Claims, against the frames"}
          </h3>
          <ul className={styles.claims}>
            {cue.claims.map((k, i) => {
              const v = VERDICT[k.verdict] ?? { word: k.verdict, ok: false };
              return (
                <li key={i} className={cx(!v.ok && styles.cut)}>
                  <span className={styles.claim}>
                    <Glyph name={v.ok ? "ok" : "out"} size={10} className={v.ok ? styles.okMark : styles.cutMark} />
                    <span>{k.text}</span>
                  </span>
                  <span className={cx(styles.verdict, v.ok ? styles.okMark : styles.cutMark)}>
                    {v.word}
                    {k.at !== null ? <span className={styles.at}> at {formatTimecode(k.at)}</span> : null}
                  </span>
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}

      {findings.length ? (
        <section className={styles.block} aria-labelledby={`${ids}-lint`}>
          <h3 id={`${ids}-lint`} className={styles.h3}>
            Writing checks
          </h3>
          <ul className={styles.findings}>
            {findings.map((f, i) => (
              <li key={i} data-severity={f.severity}>
                <span className={styles.rule}>{f.rule}</span>
                <span>{f.message}</span>
                <span className={styles.sev}>{f.severity === "error" ? "Must fix" : "Advice"}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <Provenance run={run} cue={cue} />
      {run.costUsd !== null ? (
        <p className={styles.cost}>
          Model spend for the whole run: <b>{usd(run.costUsd)}</b>. It isn’t split by line.
        </p>
      ) : null}
    </div>
  );
}
