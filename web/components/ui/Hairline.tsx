import { cx } from "@/lib/cx";
import styles from "./Hairline.module.css";

export type HairlineTone = "faint" | "default" | "strong" | "door" | "lamp";

type HairlineProps = {
  /** faint .06 (rows inside panels) · default .10 · strong .18 (panel and chip borders) · door .28 · lamp (the measured rule). */
  tone?: HairlineTone;
  /** Set when the rule is a real thematic break that assistive tech should announce. */
  semantic?: boolean;
  className?: string;
};

/** A 1px rule. Decorative by default. */
export function Hairline({ tone = "default", semantic = false, className }: HairlineProps) {
  return <hr className={cx(styles.hair, styles[tone], className)} aria-hidden={semantic ? undefined : true} />;
}
