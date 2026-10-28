import type { ReactNode } from "react";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { cx } from "@/lib/cx";
import { LIBRARY_AVAILABLE } from "@/lib/features";
import { lineCounts, slug } from "@/lib/showcase-derive";
import type { Showcase } from "@/lib/showcase-schema.mjs";
import styles from "./Outputs.module.css";

/** The authored 18px line glyphs, one per file. */
const ICON = {
  vtt: "M3 4h12M3 8h9M3 12h11M3 16h6",
  audio: "M1 9h3M5 6v6M8 3v12M11 7v4M14 5v8M17 9h-2",
  video: "M1.5 3.5h15v11h-15zM7 6.5v5l4-2.5z",
  extended: "M1.5 3.5h15v11h-15zM7 6.5v5M11 6.5v5",
  report: "M3.5 1.5h8l3 3v12h-11zM6 8l2 2 4-4M6 13h6",
  script: "M3.5 1.5h11v15h-11zM6 5h6M6 8h6M6 11h4",
} as const;

const FILES: { name: string; std: string; icon: keyof typeof ICON; desc: ReactNode }[] = [
  {
    name: "descriptions.vtt",
    std: "WebVTT",
    icon: "vtt",
    desc: (
      <>
        Every line with its timing, as a <span className={styles.code}>kind=&quot;descriptions&quot;</span> text track.
      </>
    ),
  },
  { name: "described-audio.m4a", std: "WCAG G78", icon: "audio", desc: "The description as its own audio track, for players that let viewers choose it." },
  { name: "described.mp4", std: "WCAG G173", icon: "video", desc: "The video with the description mixed into its soundtrack." },
  { name: "extended.mp4", std: "SC 1.2.7, G8", icon: "extended", desc: "Pauses the picture when a pause is too short for what needs saying." },
  { name: "report.html", std: "Conformance", icon: "report", desc: "WCAG conformance for the video, plus every model call and what it cost." },
  { name: "script.md", std: "Markdown", icon: "script", desc: "The full description script, for editing and sign-off." },
];

/**
 * Where the showcase run stands with review, from its own counts: approved only when every
 * line was, never claimed for a run a person hasn't signed off.
 */
export function reviewState(s: Pick<Showcase, "cues" | "summary">): string {
  const k = lineCounts(s);
  const all = k.passed + k.extended + (k.held ?? 0);
  if (all > 0 && k.reviewed === k.passed + k.extended && !k.held) return "Reviewed and approved in the studio";
  const parts = [k.held ? `${k.held} held` : null, k.reviewed ? `${k.reviewed} approved or edited` : "none approved yet"].filter(Boolean);
  return `Needs review in the studio: ${parts.join(", ")}`;
}

/** 6. What you ship: six files in a folder named for the showcase clip. */
export function Outputs({ showcase }: { showcase: Pick<Showcase, "title" | "cues" | "summary"> }) {
  return (
    <section id="library" aria-labelledby="library-title" className={cx(styles.sec, "ruled")}>
      <div className={cx("wrap", styles.grid)}>
        <div className={styles.copy}>
          <Eyebrow>What you ship</Eyebrow>
          <h2 id="library-title" className="t-display-2">
            Six files, in formats players already read.
          </h2>
          <p className={cx("t-lede", styles.lede)}>A text track, an audio track, two finished videos, a conformance report and the script.</p>
          <p className={styles.note}>
            <b>Each line can be reviewed in the studio before it ships.</b>
            {LIBRARY_AVAILABLE ? " Point Lull at a course library and new lectures arrive drafted, waiting for that review." : null}
          </p>
        </div>
        <div className={styles.folder}>
          <div className={styles.tab}>
            <svg width="14" height="12" viewBox="0 0 14 12" aria-hidden="true" focusable="false">
              <path d="M.5 1.5h4.5l1.5 1.5h7v8.5H.5z" fill="none" stroke="var(--lamp-1)" strokeWidth="1" />
            </svg>
            {slug(showcase.title)}/
          </div>
          <div className={styles.body}>
            <ul className={styles.files}>
              {FILES.map((f) => (
                <li key={f.name} className={styles.file}>
                  <svg viewBox="0 0 18 18" aria-hidden="true" focusable="false">
                    <path d={ICON[f.icon]} fill="none" stroke="currentColor" strokeWidth="1.2" />
                  </svg>
                  <span className={styles.name}>{f.name}</span>
                  <span className={styles.std}>{f.std}</span>
                  <span className={styles.desc}>{f.desc}</span>
                </li>
              ))}
            </ul>
            <p className={styles.foot}>
              <span>{FILES.length} files</span>
              <span data-review-state="">{reviewState(showcase)}</span>
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
