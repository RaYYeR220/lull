import type { Metadata } from "next";
import { Footer } from "@/components/chrome/Footer";
import { Nav } from "@/components/chrome/Nav";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Door } from "@/components/ui/Gate";
import { MetaList } from "@/components/ui/MetaList";
import { ForkDoor } from "@/components/watch/ForkDoor";
import { RunChecks } from "@/components/watch/RunChecks";
import { RunFiles } from "@/components/watch/RunFiles";
import { WatchRun } from "@/components/watch/WatchRun";
import { cx } from "@/lib/cx";
import Link from "next/link";
import { reportHref, studioHref } from "@/lib/routes";
import { runLines, voicedLines } from "@/lib/run-data";
import { loadIndex, loadShowcase } from "@/lib/showcase";
import { creditParts, plural, voicedCues } from "@/lib/showcase-derive";
import { loadShowcaseRun } from "@/lib/showcase-run";
import { MAIN_ID } from "@/lib/site";
import { formatTimecode } from "@/lib/timecode";
import run from "@/components/watch/Watch.module.css";
import styles from "../watch.module.css";

type Params = { id: string };

/** One page per shipped showcase, from public/showcase/index.json; any other id is a 404. */
export const dynamicParams = false;

export async function generateStaticParams(): Promise<Params[]> {
  return (await loadIndex()).map(({ id }) => ({ id }));
}

export async function generateMetadata({ params }: { params: Promise<Params> }): Promise<Metadata> {
  const { id } = await params;
  const s = await loadShowcase(id);
  return {
    title: `${s.title}, described`,
    description: `A recorded run: ${s.title} with ${plural(voicedCues(s).length, "description")} placed in its pauses, each checked against its frames.`,
  };
}

export default async function WatchPage({ params }: { params: Promise<Params> }) {
  const { id } = await params;
  const s = await loadShowcaseRun(id);
  const voiced = voicedLines(s).length;
  const extended = s.cues.filter((c) => c.kind === "extended").length;
  const lines = runLines(s);
  const credit = creditParts(s.credit ?? "");

  return (
    <>
      <Nav />
      <main id={MAIN_ID} tabIndex={-1} className={cx(styles.main, styles.runMain)}>
        <div className="wrap">
          <WatchRun
            media={s.media}
            head={
              <header className={styles.runHead}>
                <Eyebrow className={styles.eyebrow}>Recorded run</Eyebrow>
                <h1 className={cx("t-display-2", styles.runTitle)}>{s.title}</h1>
                <p className={cx("t-lede", styles.runLede)}>
                  Described ahead of time and played from the files the run produced. Nothing is sent to a model while you watch.
                </p>
                <p className={styles.credit}>
                  {credit.before}
                  {credit.licence ? (
                    <a href={credit.licence.url} rel="license noopener" target="_blank">
                      {credit.licence.label}
                      <span className="sr-only"> (opens in a new tab)</span>
                    </a>
                  ) : null}
                  {credit.after}
                </p>
                <MetaList
                  label="This run"
                  className={styles.meta}
                  items={[
                    <span key="len">
                      <b>{formatTimecode(s.duration, 0)}</b> long
                    </span>,
                    <span key="lines">
                      <b>{voiced}</b> {voiced === 1 ? "line" : "lines"} in its pauses
                    </span>,
                    ...(lines.held
                      ? [
                          <span key="held">
                            <b>{lines.held}</b> held for review
                          </span>,
                        ]
                      : []),
                    ...(extended
                      ? [
                          <span key="ext">
                            <b>{extended}</b> extended
                          </span>,
                        ]
                      : []),
                    <Link key="report" href={reportHref(s.id, "recorded")} className={styles.metaLink}>
                      Conformance report
                    </Link>,
                  ]}
                />
              </header>
            }
            label={`${s.title}, described`}
            cues={s.cues}
            heldElsewhere={Math.max(0, (lines.held ?? 0) - lines.heldListed)}
            edit={
              <section aria-labelledby="edit-title" className={run.editBlock}>
                <h2 id="edit-title" className={run.h2}>
                  Change a line
                </h2>
                <p className={run.editNote}>
                  A recorded run stays as it was made. Open it in the studio to see each line with its evidence, or make your own copy
                  to edit lines, check them again and publish your version.
                </p>
                <ForkDoor runId={s.id} title={s.title}>
                  <Door tone="dark" href={studioHref(s.id)} className={run.studioDoor}>
                    Open in Studio
                  </Door>
                </ForkDoor>
              </section>
            }
            files={<RunFiles files={s.files} />}
            checks={<RunChecks run={s} />}
          />
        </div>
      </main>
      <Footer />
    </>
  );
}
