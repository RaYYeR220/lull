import type { HTMLAttributes } from "react";
import { cx } from "@/lib/cx";
import { formatTimecode, isoDuration } from "@/lib/timecode";
import styles from "./Timecode.module.css";

type TimecodeProps = Omit<HTMLAttributes<HTMLTimeElement>, "children"> & {
  /** Position in the clip, in seconds. */
  seconds: number;
  /** Decimal places on the seconds; 0 for ruler ticks. */
  digits?: number;
};

/** A mono, tabular m:ss.dd timecode with a machine-readable duration. */
export function Timecode({ seconds, digits = 2, className, ...rest }: TimecodeProps) {
  return (
    <time className={cx(styles.tc, className)} dateTime={isoDuration(seconds)} {...rest}>
      {formatTimecode(seconds, digits)}
    </time>
  );
}
