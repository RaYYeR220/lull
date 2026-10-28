"use client";

import { useEffect } from "react";
import { Footer } from "@/components/chrome/Footer";
import { Nav } from "@/components/chrome/Nav";
import { Door, Gate } from "@/components/ui/Gate";
import { cx } from "@/lib/cx";
import { MAIN_ID } from "@/lib/site";
import styles from "./not-found.module.css";

/** A page that broke in the browser: say so in the site's own voice, and offer a way on. */
export default function PageError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  return (
    <>
      <Nav />
      <main id={MAIN_ID} tabIndex={-1} className={styles.main}>
        <div className={cx("wrap", styles.body)}>
          <h1 className="t-display-2">This page stopped working.</h1>
          <p className="t-lede">Something went wrong while it ran. Try it again, or watch a recorded run, which plays without the live service.</p>
          <Gate label="Where to go next" className={styles.gate}>
            <Door tone="lit" onClick={() => reset()}>
              Try again
            </Door>
            <Door tone="dark" href="/watch">
              Watch a recorded run
            </Door>
          </Gate>
        </div>
      </main>
      <Footer />
    </>
  );
}
