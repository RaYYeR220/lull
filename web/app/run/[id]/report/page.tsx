import type { Metadata } from "next";
import { Footer } from "@/components/chrome/Footer";
import { Nav } from "@/components/chrome/Nav";
import { LiveReport } from "@/components/report/LiveReport";
import { SHOWCASE_INDEX } from "@/lib/showcase-index";
import { MAIN_ID } from "@/lib/site";
import styles from "@/components/report/Report.module.css";

type Params = { id: string };

export const metadata: Metadata = {
  title: "Conformance report",
  description: "What a live run meets, what blocks it, and what made it.",
};

/** A live run's report: the run lives on the API, so the page loads it in the browser. */
export default async function RunReportPage({ params }: { params: Promise<Params> }) {
  const { id } = await params;
  return (
    <>
      <Nav />
      <main id={MAIN_ID} tabIndex={-1} className={styles.main}>
        <div className="wrap">
          <LiveReport id={id} index={SHOWCASE_INDEX} />
        </div>
      </main>
      <Footer />
    </>
  );
}
