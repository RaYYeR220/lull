"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { FORK_TTL_MS, editTokenRuns, readForkRecord } from "@/lib/api";
import { studioHref } from "@/lib/routes";
import styles from "@/app/studio/studio.module.css";

type Mine = { id: string; source: string | null; until: number | null };

/** The runs this browser holds the edit key for: its live runs and the copies it made. */
export function YourRuns({ titles }: { titles: Record<string, string> }) {
  const [mine, setMine] = useState<Mine[] | null>(null);

  useEffect(() => {
    const now = Date.now();
    setMine(
      editTokenRuns()
        .map((id) => {
          const fork = readForkRecord(id);
          return { id, source: fork?.source ?? null, until: fork ? fork.made + FORK_TTL_MS : null };
        })
        // a copy past its day is gone from the live service
        .filter((m) => m.until === null || m.until > now),
    );
  }, []);

  if (mine === null) return null;
  if (!mine.length)
    return <p className={styles.empty}>None yet. Make a copy of a recorded run, or describe a clip on the try-it page: its run opens here for editing.</p>;
  return (
    <ul className={styles.runs}>
      {mine.map((m) => (
        <li key={m.id} className={styles.run}>
          <Link href={studioHref(m.id)} prefetch={false} className={styles.runLink}>
            <span className={styles.runId}>{m.id}</span>
            <span className={styles.runMeta}>
              {m.source ? `A copy of ${titles[m.source] ?? m.source}` : "A live run"}
              {m.until ? `, kept until ${new Date(m.until).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}` : ""}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
