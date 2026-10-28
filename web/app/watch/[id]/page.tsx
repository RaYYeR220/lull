import { existsSync } from "node:fs";
import path from "node:path";
import type { Metadata } from "next";
import { Footer } from "@/components/chrome/Footer";
import { Nav } from "@/components/chrome/Nav";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Door } from "@/components/ui/Gate";
import { MetaList } from "@/components/ui/MetaList";
import type { ScriptCue } from "@/components/watch/CueScript";
import { ForkDoor } from "@/components/watch/ForkDoor";
import { RunChecks } from "@/components/watch/RunChecks";
import { RunFiles } from "@/components/watch/RunFiles";
import { WatchRun } from "@/components/watch/WatchRun";
import { cx } from "@/lib/cx";
import { studioHref } from "@/lib/routes";
import { keyframeUrl, loadIndex, loadShowcase, showcaseMedia, type Showcase } from "@/lib/showcase";
import { creditParts, numberedCues, plural, voicedCues, wordCount } from "@/lib/showcase-derive";
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

/** The script's rows: every cue with its name, fit and evidence frame. */
function scriptCues(s: Showcase): ScriptCue[] {
  return numberedCues(s).map((c) => {
    const frame = keyframeUrl(s.id, c.id);
    return {
      id: c.id,
      start: c.start,
      text: c.text,
      status: c.status,
      label: c.label,
      fit:
        c.kind === "extended"
          ? `video holds ${(c.end - c.start).toFixed(2)} s`
          : c.gap
            ? `${wordCount(c.text)} of ${c.gap.capacity} words`
            : plural(wordCount(c.text), "word"),
      frame: existsSync(path.join(process.cwd(), "public", ...frame.split("/").filter(Boolean))) ? frame : null,
    };
  });
}

export default async function WatchPage({ params }: { params: Promise<Params> }) {
  const { id } = await params;
  const s = await loadShowcase(id);
  const media = showcaseMedia(s);
  const cues = numberedCues(s);
  const voiced = voicedCues(s).length;
  const extended = cues.filter((c) => c.kind === "extended").length;
  const credit = creditParts(s.credit);

  return (
    <>
      <Nav />
      <main id={MAIN_ID} tabIndex={-1} className={cx(styles.main, styles.runMain)}>
        <div className="wrap">
          <WatchRun
            media={{
              src: media.video,
              descriptions: media.descriptions,
              extended: media.extended,
              describedAudio: media.describedAudio,
              cueAudioBase: media.cueAudioBase,
              cueAudioExt: media.cueAudioExt,
            }}
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
                    ...(extended
                      ? [
                          <span key="ext">
                            <b>{extended}</b> extended
                          </span>,
                        ]
                      : []),
                  ]}
                />
              </header>
            }
            label={`${s.title}, described`}
            cues={scriptCues(s)}
            edit={
              <section aria-labelledby="edit-title" className={run.editBlock}>
                <h2 id="edit-title" className={run.h2}>
                  Change a line
                </h2>
                <p className={run.editNote}>
                  A recorded run stays as it was made. Open it in the studio to see each line with its evidence, or make your own copy
                  to edit lines, check them again and publish your version.
                </p>
                <ForkDoor runId={s.id}>
                  <Door tone="dark" href={studioHref(s.id)} className={run.studioDoor}>
                    Open in Studio
                  </Door>
                </ForkDoor>
              </section>
            }
            files={<RunFiles showcase={s} />}
            checks={<RunChecks showcase={s} />}
          />
        </div>
      </main>
      <Footer />
    </>
  );
}
