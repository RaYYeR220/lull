import type { CSSProperties } from "react";
import type { Health } from "@/lib/api";
import { cx } from "@/lib/cx";
import { usdShort } from "@/lib/run-format";
import shared from "./shared.module.css";
import styles from "./ServiceStatus.module.css";

export type Service = { status: "loading" } | { status: "ok"; health: Health } | { status: "down" };

function Meter({ label, spent, cap }: { label: string; spent: number | null; cap: number | null }) {
  const capped = cap !== null && cap > 0;
  const share = spent !== null && capped ? Math.min(1, spent / cap) : 0;
  const words =
    spent === null ? "Not known" : capped ? `${usdShort(spent)} of ${usdShort(cap)}` : `${usdShort(spent)}, no cap`;
  return (
    <div className={styles.meter}>
      <dt>{label}</dt>
      <dd>
        <span className={styles.track} aria-hidden="true">
          <i className={cx(share >= 1 && styles.full)} style={{ "--share": share } as CSSProperties} />
        </span>
        <span>{words}</span>
      </dd>
    </div>
  );
}

/** The live service at a glance: whether it answers, and how much of the model budget is spent. */
export function ServiceStatus({ service }: { service: Service }) {
  const health = service.status === "ok" ? service.health : null;
  const state =
    service.status === "loading"
      ? "Checking"
      : service.status === "down"
        ? "Not answering"
        : health?.liveBusy
          ? "Busy with a description"
          : "Ready";

  return (
    <section aria-labelledby="service-title" className={styles.status}>
      <div className={styles.row}>
        <h2 id="service-title" className={shared.label}>
          Live service
        </h2>
        <p className={styles.state} data-state={service.status === "ok" ? (health?.liveBusy ? "busy" : "ok") : service.status}>
          <i aria-hidden="true" />
          {state}
        </p>
      </div>
      {health?.speech === false ? (
        <p className={styles.note}>The speech server isn’t answering, so a run may stop at Listen.</p>
      ) : null}
      {health?.budget ? (
        <dl className={styles.meters} aria-label="Model budget">
          <Meter label="Spent today" spent={health.budget.spentToday} cap={health.budget.capDay} />
          <Meter label="In total" spent={health.budget.spentTotal} cap={health.budget.capTotal} />
        </dl>
      ) : null}
    </section>
  );
}
