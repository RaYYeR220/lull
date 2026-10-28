import Link from "next/link";
import { cx } from "@/lib/cx";
import styles from "./Wordmark.module.css";

/** "lull" in Newsreader italic 300; a link home. 34px in the header (30 on phones), 64px in the footer. */
export function Wordmark({ size = "head", className }: { size?: "head" | "foot"; className?: string }) {
  return (
    <Link href="/" aria-label="Lull, home" className={cx("t-wordmark", styles.wordmark, size === "foot" && styles.foot, className)}>
      lull
    </Link>
  );
}
