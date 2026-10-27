import { memo } from "react";
import { Glyph } from "@/components/ui/Glyph";
import { STAGES, type StageName } from "@/lib/api";
import { cx } from "@/lib/cx";
import { STAGE_COPY, secs, stageDetail } from "@/lib/run-format";
import type { StageView } from "@/lib/run-state";
import styles from "./StageRail.module.css";

type Props = {
  stages: Record<StageName, StageView>;
  /** A running count for the active stage, e.g. "4 lines checked". */
  progress?: Partial<Record<StageName, string>>;
};

/** The seven stages in order: waiting, working (lit), done with its time, or stopped. */
export const StageRail = memo(function StageRail({ stages, progress }: Props) {
  return (
    <ol className={styles.rail} aria-label="Stages">
      {STAGES.map((name) => {
        const st = stages[name];
        const copy = STAGE_COPY[name];
        const detail = st.status === "done" ? stageDetail(name, st.info) : st.status === "active" ? (progress?.[name] ?? null) : null;
        return (
          <li
            key={name}
            className={cx(styles.stage, styles[st.status])}
            aria-current={st.status === "active" ? "step" : undefined}
            data-stage={name}
            data-status={st.status}
          >
            <span className={styles.name}>{copy.name}</span>
            <span className={styles.what}>{copy.what}</span>
            <span className={styles.state}>
              {st.status === "waiting" ? "Waiting" : null}
              {st.status === "active" ? (
                <>
                  <i className={styles.pulse} aria-hidden="true" />
                  Working
                </>
              ) : null}
              {st.status === "done" ? (
                <>
                  <span>
                    <span className="sr-only">Done in </span>
                    {st.seconds !== null ? secs(st.seconds) : "Done"}
                  </span>
                  {st.cached ? <span className={styles.cached}>Cached</span> : null}
                </>
              ) : null}
              {st.status === "failed" ? (
                <>
                  <Glyph name="out" size={9} />
                  Stopped
                </>
              ) : null}
            </span>
            {detail ? <span className={styles.detail}>{detail}</span> : null}
          </li>
        );
      })}
    </ol>
  );
});
