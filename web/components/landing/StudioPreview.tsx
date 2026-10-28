import type { CSSProperties, ReactNode } from "react";
import { Glyph } from "@/components/ui/Glyph";
import { Section } from "@/components/ui/Section";
import { STATUS, StatusMark } from "@/components/ui/StatusMark";
import { cx } from "@/lib/cx";
import { keyframeUrl, type CueStatus, type Showcase } from "@/lib/showcase";
import { clamp, numberedCues, plural, slug, verifiedCue, wordCount } from "@/lib/showcase-derive";
import { formatTimecode as fmt } from "@/lib/timecode";
import styles from "./StudioPreview.module.css";

/** Order statuses are counted and listed in. */
const ORDER: CueStatus[] = ["verified", "approved", "edited", "extended", "held"];
const LEGEND: Record<CueStatus, string> = {
  verified: "Verified",
  approved: "Approved",
  edited: "Edited",
  extended: "Extended",
  held: "Held for review",
};

const pct = (t: number, T: number) => `${((t / T) * 100).toFixed(3)}%`;

function Row({ kind, label, children }: { kind?: string; label: string; children: ReactNode }) {
  return (
    <div className={cx(styles.row, kind)}>
      <span className={styles.lab}>{label}</span>
      <div className={styles.track}>{children}</div>
    </div>
  );
}

/** The soundtrack as one SVG: every 100 ms frame a vertical stroke, speech brighter. */
function Waveform({ loudness, speech }: Pick<Showcase, "loudness" | "speech">) {
  let quiet = "";
  let spoken = "";
  loudness.forEach((db, i) => {
    const a = Math.pow(clamp((db + 60) / 44, 0, 1), 1.3) * 18 + 0.4;
    const seg = `M${i} ${(20 - a).toFixed(1)}V${(20 + a).toFixed(1)}`;
    const t = i / 10;
    if (speech.some(([s, e]) => t >= s - 0.05 && t <= e + 0.05)) spoken += seg;
    else quiet += seg;
  });
  return (
    <svg viewBox={`0 0 ${loudness.length} 40`} preserveAspectRatio="none" aria-hidden="true" focusable="false">
      <path d={quiet} stroke="rgba(246,238,223,.3)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
      <path d={spoken} stroke="rgba(246,238,223,.88)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** 7. The studio: a still of the review surface, drawn from the showcase run. */
export function StudioPreview({ showcase }: { showcase: Showcase }) {
  const T = showcase.duration;
  const cues = numberedCues(showcase);
  const standard = cues.filter((c) => c.kind === "standard");
  const extended = cues.filter((c) => c.kind === "extended");
  const match = verifiedCue(showcase);
  const selected = cues.find((c) => c.id === match?.id) ?? cues[0];
  const counts = ORDER.map((s) => [s, cues.filter((c) => c.status === s).length] as const).filter(([, n]) => n > 0);
  const held = cues.filter((c) => c.status === "held").length;

  const ruler: ReactNode[] = [];
  for (let t = 0; t <= T; t += 10) {
    if (t < T)
      ruler.push(
        <span key={`s${t}`} style={{ left: pct(t, T) }}>
          {fmt(t, 0)}
        </span>,
      );
    ruler.push(<i key={`i${t}`} style={{ left: pct(t, T) }} />);
  }

  const claims = match && selected?.id === match.id ? showcase.verifyExample?.claims : undefined;
  const words = selected ? wordCount(selected.text) : 0;
  const capacity = selected?.gap?.capacity;

  return (
    <Section
      id="studio"
      eyebrow="The studio"
      heading="Every line, with its evidence."
      lede="The whole timeline in one view: the soundtrack, who speaks, every pause and how much fits in it, and each line with the frames behind it. Edit a line and Lull re-fits, re-checks and re-voices just that one."
      headCols={6}
    >
      <figure className={styles.fig}>
        <div className={styles.studio}>
          <div className={styles.bar}>
            <span className={styles.brand}>
              <span className={styles.wm}>lull</span>
              <span>Studio</span>
            </span>
            <span className={styles.file}>
              <span className={styles.mono}>{slug(showcase.title)}.mp4</span>
              <span className={styles.mono}>{fmt(T)}</span>
            </span>
            <ul className={styles.counts} aria-label="Cue statuses">
              {counts.map(([s, n]) => (
                <li key={s} className={styles[s]}>
                  <Glyph name={STATUS[s].glyph} />
                  {n} {STATUS[s].word.toLowerCase()}
                </li>
              ))}
            </ul>
            <span className={styles.publish}>{held ? `Publish, ${plural(held, "cue")} held` : "Publish"}</span>
          </div>

          <div className={styles.body}>
            <div className={styles.tl} role="group" tabIndex={0} aria-label="Timeline, scrolls sideways on small screens">
              <div className={styles.tlInner}>
                <Row kind={styles.ruler} label="">
                  {ruler}
                </Row>
                <Row kind={styles.wave} label="Soundtrack">
                  <Waveform loudness={showcase.loudness} speech={showcase.speech} />
                </Row>
                <Row kind={styles.spk} label="Speakers">
                  {showcase.speech.map(([a, b]) => {
                    const said =
                      b - a >= 3 && showcase.words
                        ? showcase.words
                            .filter((w) => w.start >= a - 0.01 && w.end <= b + 0.01)
                            .map((w) => w.text)
                            .join(" ")
                        : "";
                    return (
                      <span key={a} className={cx(styles.blk, styles.blkSpk)} style={{ left: pct(a, T), width: pct(b - a, T) }}>
                        {said}
                      </span>
                    );
                  })}
                </Row>
                <Row kind={styles.gaps} label="Pauses">
                  {showcase.gaps.map((g) => (
                    <span key={g.id} className={cx(styles.blk, styles.blkGap)} style={{ left: pct(g.start, T), width: pct(g.end - g.start, T) }}>
                      <span className={styles.len}>{(g.end - g.start).toFixed(2)} s</span>
                      <span className={styles.w}>{g.capacity} words</span>
                    </span>
                  ))}
                </Row>
                <Row kind={styles.cues} label="Cues">
                  {standard.map((c) => (
                    <span
                      key={c.id}
                      className={cx(styles.blk, styles.blkCue, styles[c.status], c === selected && styles.sel)}
                      style={{ left: pct(c.start, T), width: pct(Math.max(c.end - c.start, 0.3), T) }}
                    >
                      <Glyph name={STATUS[c.status].glyph} />
                      <span className={styles.cn}>{c.label}</span>
                    </span>
                  ))}
                </Row>
                {extended.length ? (
                  // the video stops for these: a mark at each freeze point, not a span of the clip
                  <Row kind={styles.ext} label="Extended">
                    {extended.map((c) => (
                      <span key={c.id} className={cx(styles.blk, styles.blkExt, c === selected && styles.sel)} style={{ left: pct(c.start, T) }}>
                        <Glyph name="extended" />
                      </span>
                    ))}
                  </Row>
                ) : null}
                {selected?.gap ? (
                  <span className={styles.playhead} style={{ left: `calc(92px + (100% - 110px) * ${(selected.gap.start / T).toFixed(4)})` }} />
                ) : null}
              </div>
              <ul className={styles.legend} aria-hidden="true">
                {ORDER.filter((s) => counts.some(([k]) => k === s)).map((s) => (
                  <li key={s}>
                    <i className={styles[s]} />
                    {LEGEND[s]}
                  </li>
                ))}
                <li>
                  <i className={styles.legendGap} />
                  Pause, with room in words
                </li>
              </ul>
              <p className={styles.listHead}>
                <span>Cues</span>
                <span>
                  {plural(cues.length, "line")}, {plural(showcase.gaps.length, "pause")}
                </span>
              </p>
              <ol className={styles.list}>
                {cues.map((c) => (
                  <li key={c.id} className={cx(c === selected && styles.sel)}>
                    <span className={styles.n}>{c.label}</span>
                    <span className={styles.mono}>{fmt(c.start)}</span>
                    <span className={styles.ln}>{c.text}</span>
                    <span className={styles.wc}>{c.gap ? `${wordCount(c.text)} of ${c.gap.capacity}` : plural(wordCount(c.text), "word")}</span>
                    <span className={cx(styles.s, styles[c.status])}>
                      <Glyph name={STATUS[c.status].glyph} />
                      {STATUS[c.status].word}
                    </span>
                  </li>
                ))}
              </ol>
            </div>

            {selected ? (
              <div className={styles.drawer}>
                <div>
                  <div className={styles.drHead}>
                    <h3>{selected.label}</h3>
                    <StatusMark status={selected.status} variant="pill" />
                  </div>
                  <p className={styles.drTime}>
                    {selected.gap ? `window ${fmt(selected.gap.start)} to ${fmt(selected.gap.end)}` : `at ${fmt(selected.start)}`}
                  </p>
                </div>
                <figure className={styles.frame}>
                  {/* eslint-disable-next-line @next/next/no-img-element -- a 854x480 still from the export, already small */}
                  <img src={keyframeUrl(showcase.id, selected.id)} alt="" width={854} height={480} loading="lazy" decoding="async" />
                  <figcaption>
                    <span>Evidence frame</span>
                    <span className={styles.mono}>{fmt(selected.start)}</span>
                  </figcaption>
                </figure>
                <div>
                  <p className={styles.drLine}>{selected.text}</p>
                  {capacity ? (
                    <div className={styles.meter}>
                      <span className={styles.meterBar}>
                        <i style={{ width: `${Math.min(100, (words / capacity) * 100).toFixed(1)}%` } as CSSProperties} />
                      </span>
                      <span>
                        {words} of {capacity} words
                      </span>
                    </div>
                  ) : null}
                </div>
                {claims ? (
                  <ul className={styles.claims} aria-label="Claims and verdicts">
                    {claims.map((c) => {
                      const ok = c.verdict === "supported";
                      return (
                        <li key={c.text} className={cx(!ok && styles.cut)}>
                          <span>
                            <Glyph name={ok ? "ok" : "out"} className={ok ? styles.verified : styles.held} />
                            {c.text}
                          </span>
                          <span className={cx(styles.vd, ok ? styles.verified : styles.held)}>
                            {ok ? "Seen" : c.verdict === "contradicted" ? "Contradicted, cut" : "Not visible, cut"}
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                ) : null}
                <ul className={styles.chips} aria-label="Provenance">
                  <li>watched by Super VL</li>
                  <li>written by Ultra</li>
                  <li>fitted by solver</li>
                  <li>voiced by Magpie</li>
                </ul>
                <div className={styles.actions} aria-hidden="true">
                  <span className={styles.lit}>Approve</span>
                  <span>Edit line</span>
                  <span>Hold</span>
                </div>
              </div>
            ) : null}
          </div>
        </div>
        <figcaption className={styles.cap}>
          Interface preview, drawn from the real run: its soundtrack, pauses, lines, statuses and evidence frame.
        </figcaption>
      </figure>
    </Section>
  );
}
