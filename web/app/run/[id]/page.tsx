import type { Metadata } from "next";
import { Footer } from "@/components/chrome/Footer";
import { Nav } from "@/components/chrome/Nav";
import { LiveRun } from "@/components/run/LiveRun";
import { cx } from "@/lib/cx";
import { SHOWCASE_INDEX } from "@/lib/showcase-index";
import { MAIN_ID } from "@/lib/site";
import styles from "../../watch/watch.module.css";

type Params = { id: string };

export const metadata: Metadata = {
  title: "Live run",
  description: "A finished live run: the video with its descriptions, the script, what was checked, and its files.",
};

/**
 * A live run's page. The run lives on the API, so the page loads it in the browser; the shipped
 * recorded runs are passed in so a run of one of their clips can borrow the original video.
 */
export default async function RunPage({ params }: { params: Promise<Params> }) {
  const { id } = await params;
  return (
    <>
      <Nav />
      <main id={MAIN_ID} tabIndex={-1} className={cx(styles.main, styles.runMain)}>
        <div className="wrap">
          <LiveRun id={id} index={SHOWCASE_INDEX} />
        </div>
      </main>
      <Footer />
    </>
  );
}
