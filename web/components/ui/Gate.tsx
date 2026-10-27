import Link from "next/link";
import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ReactNode } from "react";
import { cx } from "@/lib/cx";
import { prefetchFor } from "@/lib/site";
import styles from "./Gate.module.css";

type DoorCommon = {
  /** lit: the cream door with the lamp glow and ledge. dark: its partner, catching the light on its edge. */
  tone: "lit" | "dark";
  /** A decorative glyph before the label. */
  icon?: ReactNode;
  children: ReactNode;
  className?: string;
};
type AnchorRest = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "className" | "children"> & { href: string };
type ButtonRest = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "className" | "children"> & { href?: undefined };

export type DoorProps = DoorCommon & (AnchorRest | ButtonRest);

/** One door of the gate: a link when given `href`, otherwise a button. */
export function Door({ tone, icon, children, className, ...rest }: DoorProps) {
  const cls = cx(styles.door, tone === "lit" ? styles.lit : styles.dark, className);
  const body = (
    <>
      {icon}
      <span>{children}</span>
    </>
  );

  if (rest.href !== undefined) {
    const { href, ...anchor } = rest as AnchorRest;
    return /^https?:\/\//.test(href) ? (
      <a href={href} className={cls} {...anchor}>
        {body}
      </a>
    ) : (
      <Link href={href} prefetch={prefetchFor(href)} className={cls} {...anchor}>
        {body}
      </Link>
    );
  }

  const { type = "button", ...button } = rest as ButtonRest;
  return (
    <button type={type} className={cls} {...button}>
      {body}
    </button>
  );
}

/** The two-door call to action: one lit door, one dark door, side by side (stacked on phones). */
export function Gate({ label, className, children }: { label: string; className?: string; children: ReactNode }) {
  return (
    <div role="group" aria-label={label} className={cx(styles.gate, className)}>
      {children}
    </div>
  );
}
