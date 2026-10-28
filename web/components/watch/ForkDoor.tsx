"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Door, Gate } from "@/components/ui/Gate";
import { ApiError, forkRun, health, isAbort, saveEditToken } from "@/lib/api";
import { studioHref } from "@/lib/routes";
import styles from "./Watch.module.css";

type Phase =
  | { kind: "checking" }
  | { kind: "ready" }
  | { kind: "down"; reason: string }
  | { kind: "making" }
  | { kind: "refused"; message: string }
  | { kind: "made" };

const DOWN = "The live service isn’t answering, so a copy can’t be made right now. This recorded run still plays.";
const NOT_READY = "The live service says it isn’t ready, so a copy can’t be made right now. This recorded run still plays.";

/**
 * "Make an editable copy": forks the recorded run on the live service (no model is called),
 * keeps the copy's edit token in this browser and opens the copy in the studio. Disabled,
 * with the reason beside it, while the service isn't answering.
 */
export function ForkDoor({ runId, children }: { runId: string; /** The door beside it in the gate. */ children?: ReactNode }) {
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
    if (phase.kind === "making" || phase.kind === "made") return;
    const ctl = new AbortController();
    busy.current = ctl;
    setPhase({ kind: "making" });
    try {
      const fork = await forkRun(runId, ctl.signal);
      saveEditToken(fork.runId, fork.editToken);
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
  const said =
    phase.kind === "making"
      ? "Making your copy…"
      : phase.kind === "made"
        ? "Copy made. Opening it in the studio."
        : phase.kind === "refused"
          ? phase.message
          : "";

  return (
    <div className={styles.fork}>
      <Gate label="Edit this run" className={styles.doors}>
        <Door
          tone="lit"
          className={styles.forkDoor}
          onClick={make}
          disabled={down || phase.kind === "making" || phase.kind === "made"}
          aria-describedby={down ? reasonId : undefined}
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
      ) : null}
      <p role="status" className={phase.kind === "refused" ? styles.refused : styles.status}>
        {said}
      </p>
    </div>
  );
}
