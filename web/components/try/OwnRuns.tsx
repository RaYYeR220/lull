"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import type { Retention } from "@/lib/api";
import { cx } from "@/lib/cx";
import { DEFAULT_RETENTION, ownRuns, type OwnRun } from "@/lib/own-runs";
import { runHref, studioHref } from "@/lib/routes";
import shared from "./shared.module.css";
import styles from "./OwnRuns.module.css";

const KIND: Record<OwnRun["kind"], string> = { sample: "A sample", upload: "Your clip", fork: "Your copy" };

/** "28 Oct, 21:45" in the visitor's own time. */
const when = (ms: number) => new Date(ms).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

/**
 * The runs this browser started or copied, to open again: the service lists no one's uploads,
 * so only this browser knows them. Nothing shows until there is one.
 */
export function OwnRuns({ retention, className }: { retention?: Retention | null; className?: string }) {
  const [rows, setRows] = useState<OwnRun[]>([]);
  const keep = { uploadDays: retention?.uploadDays ?? DEFAULT_RETENTION.uploadDays, forkHours: retention?.forkHours ?? DEFAULT_RETENTION.forkHours };
  useEffect(() => setRows(ownRuns(Date.now(), keep)), [keep.uploadDays, keep.forkHours]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!rows.length) return null;
  return (
    <section aria-labelledby="own-runs-title" className={cx(styles.own, className)} data-own-runs="">
      <h2 id="own-runs-title" className={shared.label}>
        Your recent runs
      </h2>
      <p className={styles.note}>
        Listed from this browser only: the service lists no one’s uploads. Uploads are removed after {keep.uploadDays} days, copies after {keep.forkHours} hours.
      </p>
      <ul className={styles.list}>
        {rows.map((r) => (
          <li key={r.id} className={styles.row}>
            <Link href={runHref(r.id)} prefetch={false} className={cx(shared.link, styles.title)}>
              {r.title}
            </Link>
            <span className={styles.meta}>
              {KIND[r.kind]}, {when(r.made)}
            </span>
            <Link href={studioHref(r.id)} prefetch={false} className={cx(shared.link, styles.studio)}>
              Studio<span className="sr-only">: {r.title}</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
