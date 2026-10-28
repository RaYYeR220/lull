"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Door, Gate } from "@/components/ui/Gate";
import { ApiError, forkRun, health, isAbort, saveEditToken, saveForkRecord } from "@/lib/api";
import { rememberRun } from "@/lib/own-runs";
import { studioHref } from "@/lib/routes";
import styles from "./Watch.module.css";

type Phase =
  | { kind: "checking" }
  | { kind: "ready" }
  | { kind: "down"; reason: string }
  | { kind: "making" }
  | { kind: "refused"; message: string }
  | { kind: "made" }
  /** The copy exists, but this browser wouldn't keep its edit key: it can only be opened to look at. */
  | { kind: "keyless"; runId: string };

const DOWN = "The live service isn’t answering, so a copy can’t be made right now. This recorded run still plays.";
const NOT_READY = "The live service says it isn’t ready, so a copy can’t be made right now. This recorded run still plays.";

const CHECKING = "Checking that the live service is answering…";
const KEYLESS = "Copy made, but this browser can’t keep the edit key, so you can’t edit it here. Private windows and blocked site storage do this.";

/**
 * "Make an editable copy": forks the run on the live service (no model is called), keeps the
 * copy's edit token in this browser and opens the copy in the studio. Disabled, with the
 * reason beside it, while the service is being checked or isn't answering. Copies are removed
 * a day after they are made.
 */
export function ForkDoor({
  runId,
  title,
  children,
  label = "Edit this run",
}: {
  runId: string;
  /** The run's title, for this browser's list of its own runs. */
  title?: string;
  /** The door beside it in the gate. */
  children?: ReactNode;
  label?: string;
}) {
  const router = useRouter();
  const reasonId = useId();
  const [phase, setPhase] = useState<Phase>({ kind: "checking" });
  const busy = useRef<AbortController | null>(null);
  const alive = useRef(false);

  const check = useCallback(async () => {
    try {
      const h = await health(AbortSignal.timeout(10_000));
      if (alive.current) setPhase(h.ok ? { kind: "ready" } : { kind: "down", reason: NOT_READY });
    } catch (e) {
      if (!alive.current) return;
      // nothing answered (or not in time): down; an answer that isn't a health report: not ready
      setPhase({ kind: "down", reason: e instanceof ApiError && e.code !== "unreachable" ? NOT_READY : DOWN });
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    void check();
    return () => {
      alive.current = false;
      busy.current?.abort();
    };
  }, [check]);

  const make = async () => {
    if (phase.kind !== "ready" && phase.kind !== "refused") return;
    const ctl = new AbortController();
    busy.current = ctl;
    setPhase({ kind: "making" });
    try {
      const fork = await forkRun(runId, ctl.signal);
      if (!saveEditToken(fork.runId, fork.editToken)) {
        setPhase({ kind: "keyless", runId: fork.runId });
        return;
      }
      saveForkRecord(fork.runId, { source: runId, made: Date.now() });
      rememberRun({ id: fork.runId, title: `${title ?? runId}, your copy`, kind: "fork", made: Date.now() });
      setPhase({ kind: "made" });
      router.push(studioHref(fork.runId));
    } catch (e) {
      if (isAbort(e)) return;
      if (e instanceof ApiError && (e.code === "unreachable" || e.code === "unavailable")) setPhase({ kind: "down", reason: DOWN });
      else setPhase({ kind: "refused", message: e instanceof Error ? e.message : String(e) });
    }
  };

  const recheck = () => {
    setPhase({ kind: "checking" });
    void check();
  };

  const down = phase.kind === "down";
  const checking = phase.kind === "checking";
  const said =
    phase.kind === "making"
      ? "Making your copy…"
      : phase.kind === "made"
        ? "Copy made. Opening it in the studio."
        : phase.kind === "refused"
          ? phase.message
          : phase.kind === "keyless"
            ? KEYLESS
            : "";

  return (
    <div className={styles.fork}>
      <Gate label={label} className={styles.doors}>
        <Door
          tone="lit"
          className={styles.forkDoor}
          onClick={make}
          disabled={phase.kind !== "ready" && phase.kind !== "refused"}
          aria-describedby={down || checking ? reasonId : undefined}
          data-fork=""
        >
          {phase.kind === "making" ? "Making your copy…" : "Make an editable copy"}
        </Door>
        {children}
      </Gate>
      {down ? (
        <p id={reasonId} className={styles.reason}>
          {phase.reason}{" "}
          <button type="button" className={styles.textBtn} onClick={recheck}>
            Check again
          </button>
        </p>
      ) : checking ? (
        <p id={reasonId} className={styles.reason}>
          {CHECKING}
        </p>
      ) : null}
      <p role="status" className={phase.kind === "refused" || phase.kind === "keyless" ? styles.refused : styles.status}>
        {said}
      </p>
      {phase.kind === "keyless" ? (
        <p className={styles.reason}>
          <Link href={studioHref(phase.runId)} className={styles.textLink}>
            Open the copy to look at it
          </Link>
        </p>
      ) : null}
      <p className={styles.forkNote}>A copy is yours to edit for a day, then the live service removes it.</p>
    </div>
  );
}
