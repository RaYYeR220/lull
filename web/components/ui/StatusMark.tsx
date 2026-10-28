import { cx } from "@/lib/cx";
import { Glyph, type GlyphName } from "./Glyph";
import styles from "./StatusMark.module.css";

export type Status = "verified" | "held" | "edited" | "approved" | "extended";

/** Every status is a glyph plus a word plus a colour; never colour alone (WCAG 1.4.1). */
export const STATUS: Record<Status, { word: string; glyph: GlyphName }> = {
  verified: { word: "Verified", glyph: "ok" },
  held: { word: "Held", glyph: "held" },
  edited: { word: "Edited", glyph: "edit" },
  approved: { word: "Approved", glyph: "approved" },
  extended: { word: "Extended", glyph: "extended" },
};

type StatusMarkProps = {
  status: Status;
  /** Replaces the default word, e.g. "Seen" for a verified claim. */
  label?: string;
  /** inline: glyph and word in the status colour. pill: the same inside a 24px outline. */
  variant?: "inline" | "pill";
  className?: string;
};

export function StatusMark({ status, label, variant = "inline", className }: StatusMarkProps) {
  const { word, glyph } = STATUS[status];
  return (
    <span className={cx(styles.mark, styles[variant], styles[status], className)} data-status={status}>
      <Glyph name={glyph} size={variant === "pill" ? 10 : 11} />
      <span>{label ?? word}</span>
    </span>
  );
}
