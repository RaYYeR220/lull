import type { Metadata } from "next";
import { Footer } from "@/components/chrome/Footer";
import { Nav } from "@/components/chrome/Nav";
import { RunReport } from "@/components/report/RunReport";
import { showcaseReport } from "@/lib/report-data";
import { loadIndex, loadShowcase } from "@/lib/showcase";
import { loadShowcaseRun } from "@/lib/showcase-run";
import { MAIN_ID } from "@/lib/site";
import styles from "@/components/report/Report.module.css";

type Params = { id: string };

/** One report per shipped showcase; any other id is a 404. */
export const dynamicParams = false;

export async function generateStaticParams(): Promise<Params[]> {
  return (await loadIndex()).map(({ id }) => ({ id }));
}

export async function generateMetadata({ params }: { params: Promise<Params> }): Promise<Metadata> {
  const { id } = await params;
  const s = await loadShowcase(id);
  return { title: `${s.title}: conformance report`, description: `What the recorded run of ${s.title} meets, what blocks it, and what made it.` };
}

export default async function ShowcaseReportPage({ params }: { params: Promise<Params> }) {
  const { id } = await params;
  const [s, run] = await Promise.all([loadShowcase(id), loadShowcaseRun(id)]);
  return (
    <>
      <Nav />
      <main id={MAIN_ID} tabIndex={-1} className={styles.main}>
        <div className="wrap">
          <RunReport r={showcaseReport(s, run)} />
        </div>
      </main>
      <Footer />
    </>
  );
}
