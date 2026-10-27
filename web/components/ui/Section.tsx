import type { CSSProperties, HTMLAttributes, ReactNode } from "react";
import { cx } from "@/lib/cx";
import { Eyebrow } from "./Eyebrow";
import styles from "./Section.module.css";

/** Steps of the spacing scale a section may sit on, top and bottom. */
export type SectionSpace = "section" | "none" | "s-6" | "s-7" | "s-8" | "s-9" | "s-10" | "s-11";

const space = (s: SectionSpace) => (s === "section" ? "var(--section-y)" : s === "none" ? "0px" : `var(--${s})`);

type SectionProps = Omit<HTMLAttributes<HTMLElement>, "id" | "children"> & {
  /** Anchor id; the heading gets `${id}-title` and labels the section. */
  id: string;
  eyebrow?: ReactNode;
  heading: ReactNode;
  /** Sits beside the heading, bottom-aligned; below it under 1100px. */
  lede?: ReactNode;
  /** Columns of the 12 the heading spans; the lede takes the rest. 12 for a heading with no lede. */
  headCols?: 5 | 6 | 7 | 8 | 12;
  /** The 40px ruled floor. Alternate it by section for rhythm. */
  ruled?: boolean;
  padTop?: SectionSpace;
  padBottom?: SectionSpace;
  children?: ReactNode;
};

/** A page section: eyebrow, H2 on columns 1–7, lede on 8–12, then content. */
export function Section({
  id,
  eyebrow,
  heading,
  lede,
  headCols = 7,
  ruled = false,
  padTop = "section",
  padBottom = "section",
  className,
  style,
  children,
  ...rest
}: SectionProps) {
  const titleId = `${id}-title`;
  const vars = {
    "--sec-pt": space(padTop),
    "--sec-pb": space(padBottom),
    "--head-cols": headCols,
    "--lede-start": headCols + 1,
    ...style,
  } as CSSProperties;

  return (
    <section id={id} aria-labelledby={titleId} className={cx(styles.sec, ruled && "ruled", className)} style={vars} {...rest}>
      <div className="wrap">
        {eyebrow ? <Eyebrow>{eyebrow}</Eyebrow> : null}
        <div className={styles.head}>
          <h2 id={titleId} className={cx("t-display-2", styles.title)}>
            {heading}
          </h2>
          {lede ? <p className={cx("t-lede", styles.lede)}>{lede}</p> : null}
        </div>
        {children}
      </div>
    </section>
  );
}
