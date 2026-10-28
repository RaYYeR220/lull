import type { ReactNode } from "react";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { cx } from "@/lib/cx";
import page from "@/app/watch/watch.module.css";
import styles from "./LiveRun.module.css";

/** A run that isn't there to open: a heading, plain words, and the ways on. */
export function RunNotice({ title, children, doors, eyebrow = "Live run" }: { title: string; children: ReactNode; doors: ReactNode; eyebrow?: string }) {
  return (
    <section className={styles.notice} aria-labelledby="run-state" data-run-notice="">
      <Eyebrow className={page.eyebrow}>{eyebrow}</Eyebrow>
      <h1 id="run-state" className={cx("t-display-2", styles.noticeTitle)} tabIndex={-1}>
        {title}
      </h1>
      <div className={styles.noticeBody}>{children}</div>
      <div className={styles.noticeDoors}>{doors}</div>
    </section>
  );
}
