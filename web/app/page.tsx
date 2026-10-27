import { Footer } from "@/components/chrome/Footer";
import { Nav } from "@/components/chrome/Nav";
import { Room } from "@/components/texture/Room";
import { Door, Gate } from "@/components/ui/Gate";
import { Glyph } from "@/components/ui/Glyph";
import { MetaList } from "@/components/ui/MetaList";
import { cx } from "@/lib/cx";
import { MAIN_ID, SITE } from "@/lib/site";
import styles from "./page.module.css";

export default function Home() {
  return (
    <>
      <Nav />
      <main id={MAIN_ID} tabIndex={-1}>
        <section className={styles.hero} id="top" aria-labelledby="hero-title">
          <Room />
          <div className={styles.copy}>
            <h1 id="hero-title" className={cx("t-display-1", styles.title, "reveal reveal-1")}>
              {SITE.claim}
            </h1>
            <p className={cx("t-lede", styles.lede, "reveal reveal-2")}>{SITE.description}</p>
            <Gate label="Choose how to begin" className={cx(styles.gate, "reveal reveal-3")}>
              <Door tone="lit" icon={<Glyph name="play" size={14} />} aria-describedby="gate-hint">
                Preview the timing
              </Door>
              <Door tone="dark" href="/studio">
                Open the studio
              </Door>
            </Gate>
            <p id="gate-hint" className={cx(styles.hint, "reveal reveal-4")}>
              Nothing plays until you press it.
            </p>
            <MetaList
              label="What Lull guarantees"
              align="center"
              className={cx(styles.proof, "reveal reveal-4")}
              items={[
                <span key="overlap">
                  <b>0</b> overlaps with speech
                </span>,
                "every line checked against its frames",
                "NVIDIA Nemotron on Nebius",
              ]}
            />
          </div>
          {/* the horizon mounts in this row */}
          <div className={styles.horizonSlot} aria-hidden="true" />
        </section>
      </main>
      <Footer />
    </>
  );
}
