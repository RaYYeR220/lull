import type { CSSProperties } from "react";
import { Section } from "@/components/ui/Section";
import { cx } from "@/lib/cx";
import styles from "./Problem.module.css";

/** 2. The missing half of a lecture: one moment, a silence, and two sourced facts. */
export function Problem() {
  return (
    <Section
      id="problem"
      eyebrow="The problem"
      heading="The missing half of a lecture"
      headCols={12}
      ruled
      style={{ "--sec-pt": "clamp(96px,10vw,150px)" } as CSSProperties}
    >
      <figure className={styles.quote}>
        <blockquote>
          <p className="t-quote">&ldquo;As you can see on this chart&hellip;&rdquo;</p>
        </blockquote>
        <div className={styles.silence} aria-hidden="true">
          <span className={styles.pool} />
          <span className={styles.line} />
          <span className={styles.edge} style={{ left: "41%" }} />
          <span className={styles.edge} style={{ left: "65%" }} />
          <span className={styles.note} style={{ left: "41%" }}>
            The pause where the chart should be.
          </span>
        </div>
        <figcaption className="sr-only">
          A lecturer points at a chart, then moves on. A student who can&rsquo;t see the slide hears only the pointing.
        </figcaption>
      </figure>
      <div className={styles.grid}>
        <div className={styles.text}>
          <p>
            Lecturers point. At a chart, an equation, a diagram on the board. A student who can&rsquo;t see the slide gets the
            pointing and none of the thing. Audio description puts that half back, spoken in the pauses, in a voice that never
            talks over the lecturer.
          </p>
          <p className={styles.cost}>Human description is priced per minute of video, and most lecture libraries have none.</p>
        </div>
        <dl className={styles.facts}>
          <div className={styles.fact}>
            <dt>
              <span className={cx("t-numeral-l", styles.num)}>1.2.5</span>
              <span className={styles.what}>WCAG 2.1, level AA</span>
            </dt>
            <dd>
              <q>Audio description is provided for all prerecorded video content in synchronized media.</q>
              <a className={styles.src} href="https://www.w3.org/WAI/WCAG21/Understanding/audio-description-prerecorded.html">
                W3C, Understanding 1.2.5
              </a>
            </dd>
          </div>
          <div className={styles.fact}>
            <dt>
              <span className={cx("t-numeral-l", styles.num)}>Apr 26, 2027</span>
              <span className={styles.what}>ADA Title II</span>
            </dt>
            <dd>
              <span>US public universities must meet WCAG 2.1 AA from this date, course video behind a login included.</span>
              <a className={styles.src} href="https://www.federalregister.gov/d/2026-07663">
                Federal Register, Apr 20, 2026
              </a>
            </dd>
          </div>
        </dl>
      </div>
    </Section>
  );
}
