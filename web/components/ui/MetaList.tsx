import type { ReactNode } from "react";
import { cx } from "@/lib/cx";
import styles from "./MetaList.module.css";

type MetaListProps = {
  items: ReactNode[];
  /** Names the list for assistive tech, e.g. "What Lull guarantees". */
  label?: string;
  align?: "start" | "center";
  className?: string;
};

/** A meta line: short facts separated by 3px deep-lamp squares, stacked on phones. */
export function MetaList({ items, label, align = "start", className }: MetaListProps) {
  return (
    <ul aria-label={label} className={cx(styles.meta, align === "center" && styles.center, className)}>
      {items.map((item, i) => (
        <li key={i}>{item}</li>
      ))}
    </ul>
  );
}
