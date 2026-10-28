import { Glyph } from "@/components/ui/Glyph";
import { Section } from "@/components/ui/Section";
import { cx } from "@/lib/cx";
import type { Showcase, Verdict } from "@/lib/showcase";
import { draftSegments, spokenFromDraft, verifiedCue } from "@/lib/showcase-derive";
import { StrikeOnView } from "./StrikeOnView";
import styles from "./Refusal.module.css";

const MARK: Record<Verdict, { word: string; aside: string }> = {
  supported: { word: "Seen", aside: " (supported)" },
  not_visible: { word: "Not visible", aside: " (not visible in the frames, removed)" },
  contradicted: { word: "Contradicted", aside: " (contradicted by the frames, removed)" },
};

/** 5. The refusal: a draft marked claim by claim, the unsupported claim struck, then what was spoken. */
export function Refusal({ showcase }: { showcase: Showcase }) {
  const v = showcase.verifyExample;
  const match = verifiedCue(showcase);
  const cut = v ? v.claims.filter((c) => c.verdict !== "supported").length : 0;

  return (
    <Section id="refusal" eyebrow="The refusal" heading="A line it can’t prove, it doesn’t say." headCols={8}>
      <StrikeOnView className={styles.sheet}>
        {v ? (
          <div className={styles.main}>
            <p className={styles.label}>
              <b>{match ? `Draft, ${match.label.toLowerCase()}` : "Draft"}</b>
              <span>Checked claim by claim against its frames</span>
            </p>
            <p className={cx("t-proof", styles.draft)}>
              {draftSegments(v).map((seg, i) => {
                if (!seg.verdict) return seg.text;
                const ok = seg.verdict === "supported";
                return (
                  <span key={i} className={cx(styles.claim, ok ? styles.ok : styles.out)}>
                    <span className={styles.mark} aria-hidden="true">
                      <Glyph name={ok ? "ok" : "out"} />
                      {MARK[seg.verdict].word}
                    </span>
                    <span className={styles.claimText}>{seg.text}</span>
                    <span className="sr-only">{MARK[seg.verdict].aside}</span>
                  </span>
                );
              })}
            </p>
            <p className={styles.arrow}>
              {cut === 0
                ? "Every claim was seen. The line is voiced."
                : `Unsupported ${cut === 1 ? "claim" : "claims"} removed. The line is re-checked, then voiced.`}
            </p>
            <div className={styles.final}>
              <span className={styles.k}>
                <Glyph name="voice" />
                Spoken
              </span>
              <p className="t-proof-spoken">{match?.text ?? spokenFromDraft(v)}</p>
            </div>
          </div>
        ) : null}
        <div className={styles.side}>
          <p>
            Nemotron 3.5 Super VL takes another look at the exact frames the line came from and rules on each claim. Whatever it
            can&rsquo;t see is cut before a voice is made.
          </p>
          <ul className={styles.verdicts} aria-label="Possible verdicts">
            <li>
              <Glyph name="ok" className={styles.vOk} />
              <span>
                <span className={styles.vOk}>Seen.</span> Kept.
              </span>
            </li>
            <li>
              <Glyph name="out" className={styles.vOut} />
              <span>
                <span className={styles.vOut}>Not visible or contradicted.</span> Cut, and the line is checked again.
              </span>
            </li>
            <li>
              <Glyph name="held" className={styles.vOut} />
              <span>
                <span className={styles.vHeld}>Still failing.</span> Held for a person; never published on its own.
              </span>
            </li>
          </ul>
        </div>
      </StrikeOnView>
    </Section>
  );
}
