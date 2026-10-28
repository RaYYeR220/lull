import type { Metadata } from "next";
import { Footer } from "@/components/chrome/Footer";
import { Nav } from "@/components/chrome/Nav";
import { TryApp } from "@/components/try/TryApp";
import { cx } from "@/lib/cx";
import { listRecorded } from "@/lib/recorded";
import { MAIN_ID } from "@/lib/site";
import styles from "./page.module.css";

export const metadata: Metadata = {
  title: "Try it",
  description:
    "Describe a sample or your own short clip and watch each stage, model call and checked line arrive live, with the cost of every call.",
};

export default async function TryPage() {
  const recorded = await listRecorded();
  return (
    <>
      <Nav />
      <main id={MAIN_ID} tabIndex={-1} className={styles.main}>
        <div className="wrap">
          <TryApp
            recorded={recorded}
            intro={
              <>
                <h1 className={cx("t-display-2", styles.title)}>Watch a description being made.</h1>
                <p className={cx("t-lede", styles.lede)}>
                  Pick a sample or upload a short clip. Each stage, each model call with its cost, and each line as it passes
                  or fails its check against the frames shows up here as it happens.
                </p>
                <p className={styles.howLong} data-how-long="">
                  A live run takes about six minutes for a 45-second clip. You can leave this page: the run keeps going, and this browser
                  can open it when it&rsquo;s finished (or resume it if it ever stopped).
                </p>
              </>
            }
          />
        </div>
      </main>
      <Footer />
    </>
  );
}
