import type { ReactNode } from "react";
import { cx } from "@/lib/cx";
import styles from "./Eyebrow.module.css";

/** Sentence-case label with a 24px lamp rule before it. Never all caps. */
export function Eyebrow({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cx(styles.eyebrow, className)}>{children}</p>;
}
