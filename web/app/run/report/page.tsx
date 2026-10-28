import type { Metadata } from "next";
import { Footer } from "@/components/chrome/Footer";
import { Nav } from "@/components/chrome/Nav";
import { RunShell } from "@/components/run/RunShell";
import { SHOWCASE_INDEX } from "@/lib/showcase-index";
import { MAIN_ID } from "@/lib/site";
import styles from "@/components/report/Report.module.css";

export const metadata: Metadata = {
  title: "Conformance report",
  description: "What a live run meets, what blocks it, and what made it.",
};

/** A live run's report: one static shell for every /run/<id>/report; the run loads in the browser. */
export default function RunReportPage() {
  return (
    <>
      <Nav />
      <main id={MAIN_ID} tabIndex={-1} className={styles.main}>
        <div className="wrap">
          <RunShell kind="report" index={SHOWCASE_INDEX} />
        </div>
      </main>
      <Footer />
    </>
  );
}
