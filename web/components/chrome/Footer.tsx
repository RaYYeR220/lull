import Link from "next/link";
import { cx } from "@/lib/cx";
import { FOOTER_LINKS, prefetchFor } from "@/lib/site";
import { Wordmark } from "./Wordmark";
import styles from "./Footer.module.css";

/** The page ends on a flat line under one lamp pool. */
export function Footer() {
  return (
    <footer className={styles.foot}>
      <div className={styles.line} aria-hidden="true" />
      <div className={cx("wrap", styles.grid)}>
        <div className={styles.brand}>
          <Wordmark size="foot" />
          <p>Open source under Apache-2.0. Built on Nebius Token Factory with NVIDIA open models.</p>
        </div>
        <ul className={styles.links}>
          {FOOTER_LINKS.map((link) => (
            <li key={link.label}>
              {link.href.startsWith("/") ? (
                <Link href={link.href} prefetch={prefetchFor(link.href)}>
                  {link.label}
                </Link>
              ) : (
                <a href={link.href}>{link.label}</a>
              )}
            </li>
          ))}
        </ul>
        <p className={styles.fine}>
          <span>
            Demo clip: <cite>Elephants Dream</cite> (2006), Blender Foundation, CC BY 2.5.
          </span>
          <span>Nothing on this page plays sound until you press it.</span>
        </p>
      </div>
    </footer>
  );
}
