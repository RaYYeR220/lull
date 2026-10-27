import type { Metadata } from "next";
import { Footer } from "@/components/chrome/Footer";
import { Nav } from "@/components/chrome/Nav";
import { Door, Gate } from "@/components/ui/Gate";
import { cx } from "@/lib/cx";
import { MAIN_ID } from "@/lib/site";
import styles from "./not-found.module.css";

export const metadata: Metadata = { title: "Not found" };

export default function NotFound() {
  return (
    <>
      <Nav />
      <main id={MAIN_ID} tabIndex={-1} className={styles.main}>
        <div className={cx("wrap", styles.body)}>
          <h1 className="t-display-2">There’s nothing at this address.</h1>
          <p className="t-lede">Check the link, or start again from the home page.</p>
          <Gate label="Where to go next" className={styles.gate}>
            <Door tone="lit" href="/">
              Go to the home page
            </Door>
          </Gate>
        </div>
      </main>
      <Footer />
    </>
  );
}
