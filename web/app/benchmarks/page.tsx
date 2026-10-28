import type { Metadata } from "next";
import { Footer } from "@/components/chrome/Footer";
import { Nav } from "@/components/chrome/Nav";
import { BenchmarksView } from "@/components/benchmarks/BenchmarksView";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { loadBenchmarks } from "@/lib/benchmarks";
import { cx } from "@/lib/cx";
import { MAIN_ID, REPO_URL } from "@/lib/site";
import styles from "@/components/benchmarks/Benchmarks.module.css";

export const metadata: Metadata = {
  title: "Benchmarks",
  description:
    "How Lull is evaluated: pre-registered tests on synthetic lectures and against human audio description, two controls of the frame check, every number with its interval, failures included.",
};

/** The evaluation's own write-up, in the repository. */
const README = `${REPO_URL}/blob/main/eval/README.md`;

/** What each suite measures, from eval/README.md: said before any number, and in the empty state. */
function MethodSummary() {
  return (
    <div className={styles.method}>
      <p>
        <b>Suite A</b> is twelve short synthetic lectures, 3–4 slides each with two charts, whose narration leaves facts on the slides unsaid. An
        answer key lists every visual fact, the facts the narration does say, and decoys that appear nowhere in the video. The key was committed
        and tagged before the scorer existed and before any run; the scorer refuses to run unless the key still hashes to the tagged value.
      </p>
      <p>
        Lull gets only the video. The scorer then measures: description over narration (must be 0), lines that leave their pause, visual facts
        described with and without the extended track, claims the key doesn’t support, decoys stated, lines that only repeat the narration, and
        model spend and processing time per minute. A model judge decides what a line conveys; a second judge from another model family checks
        it, and their agreement is reported. Shares are pooled over the videos with 95 % bootstrap intervals.
      </p>
      <p>
        <b>Suite B</b> compares Lull with human audio description of the same videos: two people’s descriptions of Elephants Dream, and the
        U.S. Geological Survey’s own described versions of three of its videos. Each describer’s lines were split into facts before any run, and
        the judge decides which of them Lull’s lines convey, within a window around where the describer said it and anywhere in the video. On
        Elephants Dream each describer is also scored against the other, which gives a human ceiling.
      </p>
      <p>
        <b>Two controls</b> test the frame check itself. The negative control checks published lines again against footage they don’t
        describe and counts how many still pass. The ablation runs Suite A again with the check turned off and compares the two, video by video.
      </p>
    </div>
  );
}

export default async function BenchmarksPage() {
  const data = await loadBenchmarks();
  return (
    <>
      <Nav />
      <main id={MAIN_ID} tabIndex={-1} className={styles.main}>
        <div className="wrap">
          <header className={styles.head}>
            <Eyebrow className={styles.eyebrow}>Benchmarks</Eyebrow>
            <h1 className={cx("t-display-2", styles.title)}>What Lull gets right, and what it misses.</h1>
            <p className={cx("t-lede", styles.headLede)}>
              Measured on a test whose answers were fixed before anything ran. The method comes first; every share has its interval, and the failures
              are listed with the rest.
            </p>
          </header>
          <MethodSummary />
          <BenchmarksView
            data={data}
            readme={README}
            empty={
              <section aria-labelledby="empty-title" className={styles.empty}>
                <h2 id="empty-title" className={styles.h2}>
                  The evaluation hasn’t been published yet.
                </h2>
                <p className={styles.lede}>
                  Its numbers will appear here as the scorer writes them: with the pre-registration tag, the answer key’s hash and the commit they were
                  scored at.
                </p>
                <p className={styles.lede}>
                  <a href={README} className={styles.link}>
                    Read how it is measured
                  </a>
                </p>
              </section>
            }
          />
        </div>
      </main>
      <Footer />
    </>
  );
}
