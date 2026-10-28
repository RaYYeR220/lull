import type { Metadata } from "next";
import { Footer } from "@/components/chrome/Footer";
import { Nav } from "@/components/chrome/Nav";
import { StudioApp } from "@/components/studio/StudioApp";
import { cx } from "@/lib/cx";
import { loadIndex, loadShowcase } from "@/lib/showcase";
import { SHOWCASE_INDEX } from "@/lib/showcase-index";
import { loadShowcaseRun } from "@/lib/showcase-run";
import { MAIN_ID } from "@/lib/site";
import styles from "../studio.module.css";

type Params = { id: string };

/** The recorded runs are built ahead; any other id is a run on the API, loaded in the browser. */
export async function generateStaticParams(): Promise<Params[]> {
  return (await loadIndex()).map(({ id }) => ({ id }));
}

export async function generateMetadata({ params }: { params: Promise<Params> }): Promise<Metadata> {
  const { id } = await params;
  const entry = SHOWCASE_INDEX.find((e) => e.id === id);
  return {
    title: entry ? `${entry.title} in the studio` : "Studio",
    description: "Review each description line with its evidence, edit it and have it checked again, approve or hold it, then publish.",
  };
}

export default async function StudioPage({ params }: { params: Promise<Params> }) {
  const { id } = await params;
  // a recorded run is built ahead from its files (generateStaticParams); any other id reads no file
  const recorded = SHOWCASE_INDEX.some((e) => e.id === id);
  const [initial, clip] = recorded ? await Promise.all([loadShowcaseRun(id), loadShowcase(id)]) : [null, null];

  return (
    <>
      <Nav />
      <main id={MAIN_ID} tabIndex={-1} className={cx(styles.main)}>
        <div className="wrap">
          <StudioApp id={id} initial={initial} clip={clip} index={SHOWCASE_INDEX} />
        </div>
      </main>
      <Footer />
    </>
  );
}
