import { memo, type CSSProperties } from "react";
import { Glyph, type GlyphName } from "@/components/ui/Glyph";
import type { CueStatus } from "@/lib/api";
import { cx } from "@/lib/cx";
import { clock, tokens } from "@/lib/run-format";
import type { CueView, Heard, StageStatus } from "@/lib/run-state";
import shared from "./shared.module.css";
import styles from "./LiveTimeline.module.css";

const GLYPH: Record<CueStatus, GlyphName> = { verified: "ok", held: "held", edited: "edit", approved: "approved", extended: "extended" };

/** Ruler step for a clip length: about six to nine labels. */
function stepFor(duration: number): number {
  if (duration <= 20) return 2;
  if (duration <= 45) return 5;
  if (duration <= 100) return 10;
  if (duration <= 200) return 20;
  return 30;
}

type Props = {
  /** Seconds; null until the run event says. */
  duration: number | null;
  listen: StageStatus;
  heard: Heard | null;
  cues: CueView[];
  /** True while finalize is checking lines: draws how far it has got. */
  checking: boolean;
  /** The cue that landed last (outlined). */
  latestId: string | null;
};

/** What the band says once listen is done: the pauses and the room in them. */
function pauseWords(heard: Heard | null): string {
  if (!heard || heard.pauses === null) return "Heard";
  const pauses = `${heard.pauses} ${heard.pauses === 1 ? "pause" : "pauses"}`;
  return heard.capacityWords !== null ? `${pauses}, room for ${tokens(heard.capacityWords)} words` : pauses;
}

/** What the band and the blocks show, in words (the drawing itself is hidden from screen readers). */
function Caption({ listen, heard, cues }: { listen: StageStatus; heard: Heard | null; cues: CueView[] }) {
  if (listen === "waiting") return <>The soundtrack hasn’t been heard yet.</>;
  if (listen === "active") return <>Listening for speech and the pauses in it.</>;
  if (listen === "failed") return <>Listening stopped before the pauses were found.</>;
  const speech =
    heard?.hasAudio === false ? "No soundtrack." : heard?.speechSeconds != null ? `${heard.speechSeconds.toFixed(1)} s of speech.` : "";
  const held = cues.filter((c) => c.status === "held").length;
  const lines = cues.length
    ? `${cues.length} ${cues.length === 1 ? "line" : "lines"} so far${held ? `, ${held} held` : ""}.`
    : "No lines checked yet.";
  return (
    <>
      <span className="sr-only">{pauseWords(heard)}. </span>
      {speech} {lines}
    </>
  );
}

/**
 * The clip as a mini horizon: a ruler, what listen heard (counts only: the stream carries no
 * positions), and each cue landing where it plays, by status, as it passes or fails its check.
 * Drawn for sighted viewers; the caption and the cue list carry the same facts in words.
 */
export const LiveTimeline = memo(function LiveTimeline({ duration, listen, heard, cues, checking, latestId }: Props) {
  const ends = cues.map((c) => c.start + (c.audio_dur ?? 0));
  const span = duration && duration > 0 ? duration : Math.max(1, ...ends);
  const at = (t: number) => `${Math.min(100, Math.max(0, (t / span) * 100))}%`;
  const step = stepFor(span);
  const ticks: number[] = [];
  for (let t = 0; t <= span + 1e-6; t += step) ticks.push(t);
  const standardEnds = cues.filter((c) => !c.extended).map((c) => c.start + (c.audio_dur ?? 0));
  const reached = checking && standardEnds.length ? Math.max(...standardEnds) : null;

  return (
    <figure className={styles.tl} aria-labelledby="tl-title" aria-describedby="tl-caption">
      <div className={styles.head}>
        <h3 id="tl-title" className={shared.label}>
          Timeline
        </h3>
        <figcaption id="tl-caption" className={styles.caption}>
          <Caption listen={listen} heard={heard} cues={cues} />
        </figcaption>
      </div>

      <div className={styles.lanes} aria-hidden="true">
        <span className={styles.lab}>Time</span>
        <div className={cx(styles.track, styles.ruler)}>
          {duration
            ? ticks.map((t, i) => (
                <span key={t} className={cx(styles.tick, i % 2 === 1 && styles.minor)} style={{ left: at(t) }}>
                  <b className={shared.mono}>{clock(t)}</b>
                </span>
              ))
            : null}
        </div>

        <span className={styles.lab}>Heard</span>
        <div className={cx(styles.track, styles.heard)} data-listen={listen}>
          <span className={styles.heardText}>
            {listen === "waiting" ? "Not heard yet" : listen === "active" ? "Listening" : listen === "failed" ? "Stopped" : pauseWords(heard)}
          </span>
        </div>

        <span className={styles.lab}>Cues</span>
        <div className={cx(styles.track, styles.cues)}>
          {cues.map((c) => {
            const style = { "--l": at(c.start), "--w": at(c.audio_dur ?? 1.2) } as CSSProperties;
            return (
              <span
                key={c.id}
                className={cx(c.extended ? styles.ext : styles.blk, styles[c.status], c.id === latestId && styles.latest)}
                style={style}
                data-cue={c.id}
                data-status={c.status}
              >
                <Glyph name={GLYPH[c.status]} size={9} />
                {c.extended ? null : <b className={styles.num}>{c.n}</b>}
              </span>
            );
          })}
        </div>

        {reached !== null ? (
          <div className={styles.over}>
            <i className={styles.sweep} style={{ left: at(reached) }} />
          </div>
        ) : null}
      </div>
    </figure>
  );
});
