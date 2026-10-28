import type { Metadata } from "next";
import Link from "next/link";
import { Footer } from "@/components/chrome/Footer";
import { Nav } from "@/components/chrome/Nav";
import { YourRuns } from "@/components/studio/YourRuns";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { cx } from "@/lib/cx";
import { studioHref } from "@/lib/routes";
import { loadIndex } from "@/lib/showcase";
import { MAIN_ID } from "@/lib/site";
import { formatTimecode } from "@/lib/timecode";
import styles from "./studio.module.css";

export const metadata: Metadata = {
  title: "Studio",
  description: "Where a person reviews each description line with its evidence, fixes it, and publishes.",
};

/** The studio's front page: the recorded runs to open, and the runs this browser can edit. */
export default async function StudioIndex() {
  const index = await loadIndex();
  const titles = Object.fromEntries(index.map((e) => [e.id, e.title]));
  return (
    <>
      <Nav />
      <main id={MAIN_ID} tabIndex={-1} className={styles.main}>
        <div className="wrap">
          <header className={styles.head}>
            <Eyebrow className={styles.eyebrow}>Studio</Eyebrow>
            <h1 className="t-display-2">A person signs off every line.</h1>
            <p className={cx("t-lede", styles.lede)}>
              Each line sits on the timeline in its pause, with the frame it was checked against and what the check found. Edit a line and it is
              checked and voiced again; approve it, hold it, then publish. Nothing that overlaps speech gets through.
            </p>
          </header>

          <h2 className={styles.h2}>Your runs in this browser</h2>
          <p className={styles.note}>Runs you started and copies you made, while this browser keeps their edit key. Copies are kept for a day.</p>
          <YourRuns titles={titles} />

          <h2 className={styles.h2}>Recorded runs</h2>
          <p className={styles.note}>Open one to see every line with its evidence. They are read-only; make a copy from inside to edit it.</p>
          <ul className={styles.runs}>
            {index.map((e) => (
              <li key={e.id} className={styles.run}>
                <Link href={studioHref(e.id)} className={styles.runLink}>
                  <span className={styles.runTitle}>{e.title}</span>
                  <span className={styles.runMeta}>
                    {formatTimecode(e.duration, 0)} long, {e.cues} lines{e.held ? `, ${e.held} held for review` : ""}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      </main>
      <Footer />
    </>
  );
}
