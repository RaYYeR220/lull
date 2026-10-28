import { existsSync } from "node:fs";
import path from "node:path";
import type { Metadata } from "next";
import Link from "next/link";
import { Footer } from "@/components/chrome/Footer";
import { Nav } from "@/components/chrome/Nav";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { cx } from "@/lib/cx";
import { watchHref } from "@/lib/routes";
import { keyframeUrl, loadIndex, loadShowcase } from "@/lib/showcase";
import { creditParts, lineCounts, numberedCues, plural, spend } from "@/lib/showcase-derive";
import { MAIN_ID } from "@/lib/site";
import { formatTimecode, isoDuration } from "@/lib/timecode";
import styles from "./watch.module.css";

export const metadata: Metadata = {
  title: "Recorded runs",
  description: "Clips Lull has already described, each with its player, its description script and the files the run produced.",
};

/** The still for a run's card: the evidence frame of its first standard line, else of its first line. */
function cardFrame(id: string, cues: { id: string; kind: string }[]): string | null {
  const first = cues.find((c) => c.kind === "standard") ?? cues[0];
  if (!first) return null;
  const url = keyframeUrl(id, first.id);
  return existsSync(path.join(process.cwd(), "public", ...url.split("/").filter(Boolean))) ? url : null;
}

export default async function WatchIndex() {
  const index = await loadIndex();
  const shows = await Promise.all(index.map((e) => loadShowcase(e.id)));

  return (
    <>
      <Nav />
      <main id={MAIN_ID} tabIndex={-1} className={styles.main}>
        <div className="wrap">
          <header className={styles.head}>
            <Eyebrow className={styles.eyebrow}>Library</Eyebrow>
            <h1 className={cx("t-display-2", styles.title)}>Recorded runs, ready to watch.</h1>
            <p className={cx("t-lede", styles.lede)}>
              Clips Lull has already described. Each one plays from the files its run produced, with the description script beside it
              and the checks it passed.
            </p>
          </header>

          <ol className={styles.shelf}>
            {shows.map((s) => {
              const cues = numberedCues(s);
              const lines = cues.filter((c) => c.kind === "standard" && c.status !== "held").length;
              const { held, extended } = lineCounts(s); // a held extended line is held, not extended
              const frame = cardFrame(s.id, cues);
              const credit = creditParts(s.credit);
              return (
                <li key={s.id} className={styles.card}>
                  <div className={styles.still}>
                    {frame ? (
                      // eslint-disable-next-line @next/next/no-img-element -- the run's own 854x480 still
                      <img src={frame} alt="" width={854} height={480} loading="lazy" decoding="async" />
                    ) : null}
                    <time className={styles.len} dateTime={isoDuration(s.duration)}>
                      {formatTimecode(s.duration, 0)}
                    </time>
                  </div>
                  <div className={styles.cardBody}>
                    <h2 className={styles.cardTitle}>
                      <Link href={watchHref(s.id)} className={styles.cardLink}>
                        {s.title}
                      </Link>
                    </h2>
                    <p className={styles.cardCredit}>
                      {credit.before}
                      {credit.licence?.label}
                      {credit.after}
                    </p>
                    <ul className={styles.cardFacts} aria-label="This run">
                      <li>{plural(lines, "line")} in its pauses</li>
                      {held ? <li>{held} held for review</li> : null}
                      {extended ? <li>{extended} extended</li> : null}
                      {s.costUsd !== undefined ? <li>{spend(s.costUsd).figure} in model calls</li> : null}
                    </ul>
                  </div>
                </li>
              );
            })}
          </ol>
        </div>
      </main>
      <Footer />
    </>
  );
}
