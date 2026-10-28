"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { LiveReport } from "@/components/report/LiveReport";
import type { ShowcaseIndexEntry } from "@/lib/showcase-schema.mjs";
import { runIdFromPath } from "@/lib/run-path";
import { LiveRun } from "./LiveRun";
import styles from "./RunShell.module.css";

/**
 * A live run's page or report, from one static shell: the id is read from the address once
 * the page is in the browser (every /run/<id> is served this same page), then the run loads
 * from the API as before.
 */
export function RunShell({ kind, index }: { kind: "run" | "report"; index: readonly ShowcaseIndexEntry[] }) {
  const pathname = usePathname();
  const [id, setId] = useState<string | null | undefined>(undefined);
  // the address bar, not the shell's own path; again on every move between runs
  useEffect(() => setId(runIdFromPath(window.location.pathname)), [pathname]);

  if (id === undefined) return <p className={styles.wait}>Loading the run.</p>;
  if (id === null)
    return (
      <p className={styles.wait} role="status">
        This address doesn’t name a run. A run’s page is /run/ followed by its id.
      </p>
    );
  return kind === "run" ? <LiveRun key={id} id={id} index={index} /> : <LiveReport key={id} id={id} index={index} />;
}
