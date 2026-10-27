import { cx } from "@/lib/cx";
import styles from "./Room.module.css";

/**
 * The page-wide grain: fixed above the ground, under nothing interactive.
 * Rendered once, by the root layout.
 */
export function Grain() {
  return <div className={styles.grain} data-grain="" aria-hidden="true" />;
}

/**
 * The listening room behind the hero: two cool pools, a vignette, the lamp
 * and its cone (both breathing on a 30 s loop), the ruled floor and a fade
 * back into the ground so the hero meets the page without a seam.
 * Fills its nearest positioned ancestor. `body.is-still` pauses the lamp.
 */
export function Room({ className }: { className?: string }) {
  return (
    <div className={cx(styles.room, className)} aria-hidden="true">
      <div className={styles.cone}>
        <i />
      </div>
      <div className={styles.lamp} />
      <div className={styles.floor} />
    </div>
  );
}
