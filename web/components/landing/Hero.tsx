import { Room } from "@/components/texture/Room";
import { Door, Gate } from "@/components/ui/Gate";
import { MetaList } from "@/components/ui/MetaList";
import { cx } from "@/lib/cx";
import type { Showcase } from "@/lib/showcase";
import { describeSpeech, numberWord, speechOverlap, voicedCues } from "@/lib/showcase-derive";
import { SITE } from "@/lib/site";
import { Horizon, type HorizonData } from "./Horizon";
import { HorizonProvider } from "./HorizonProvider";
import { PreviewDoor } from "./PreviewDoor";
import styles from "./Hero.module.css";

const list = new Intl.ListFormat("en", { type: "conjunction" });

/** Only what the horizon draws goes to the client. */
function horizonData(s: Showcase): HorizonData {
  const voiced = voicedCues(s);
  const seconds = Math.round(s.duration);
  const pauses = list.format(s.gaps.map((g) => `${(g.end - g.start).toFixed(2)} seconds with room for ${g.capacity} words`));
  const placed = new Set(voiced.map((c) => c.gap.id)).size;
  const label =
    `${seconds} seconds of ${s.title} drawn as one line. ` +
    `The line moves where someone speaks: ${describeSpeech(s.speech)}. ` +
    `It lies flat in ${numberWord(s.gaps.length)} pauses, each lit from above: ${pauses}. ` +
    `Press play to hear where Lull places a description, in ${numberWord(placed)} of them.`;
  return {
    duration: s.duration,
    title: s.title,
    loudness: s.loudness,
    speech: s.speech,
    gaps: s.gaps,
    cues: voiced.map((c) => ({ id: c.id, start: c.start, end: c.end, text: c.text, gapId: c.gap.id, gapStart: c.gap.start, gapEnd: c.gap.end })),
    audio: s.audio,
    label,
  };
}

/** 1. The listening room: the claim, the gate, the proof line, and the horizon on the floor. */
export function Hero({ showcase }: { showcase: Showcase }) {
  const overlaps = speechOverlap(showcase).cues;
  return (
    <section className={styles.hero} id="top" aria-labelledby="hero-title">
      <Room />
      <HorizonProvider duration={showcase.duration}>
        <div className={styles.copy}>
          <h1 id="hero-title" className={cx("t-display-1", styles.title, "reveal reveal-1")}>
            {SITE.claim}
          </h1>
          <p className={cx("t-lede", styles.lede, "reveal reveal-2")}>{SITE.description}</p>
          <Gate label="Choose how to begin" className={cx(styles.gate, "reveal reveal-3")}>
            <PreviewDoor describedBy="gate-hint" />
            <Door tone="dark" href="/try">
              Describe your own clip
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
                <b>{overlaps}</b> overlaps with speech
              </span>,
              "every line checked against its frames",
              "NVIDIA Nemotron on Nebius",
            ]}
          />
        </div>
        <Horizon data={horizonData(showcase)} />
      </HorizonProvider>
    </section>
  );
}
