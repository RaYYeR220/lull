import { memo, useMemo, type CSSProperties } from "react";
import type { CallEvent } from "@/lib/api";
import { cx } from "@/lib/cx";
import { modelName, roleName, secs, tokens, usd } from "@/lib/run-format";
import { totalsByModel } from "@/lib/run-state";
import shared from "./shared.module.css";
import styles from "./CallFeed.module.css";

const isLookup = (c: CallEvent) => c.tokens_in === 0 && c.tokens_out === 0;

const CallRow = memo(function CallRow({ call, newest }: { call: CallEvent; newest: boolean }) {
  return (
    <li className={cx(styles.call, newest && styles.newest)}>
      <span className={styles.role}>{roleName(call.role)}</span>
      <span className={cx(shared.mono, styles.model)}>{modelName(call.model)}</span>
      <span className={styles.tok}>
        {isLookup(call) ? "Web lookup" : `${tokens(call.tokens_in)} in, ${tokens(call.tokens_out)} out`}
      </span>
      <span className={styles.cost}>{usd(call.cost_usd)}</span>
      <span className={styles.lat}>
        <span className="sr-only">took </span>
        {secs(call.latency_s)}
      </span>
    </li>
  );
});

/** Which model did what: the spend so far, per model, then every call, newest first. */
export const CallFeed = memo(function CallFeed({ calls, cost }: { calls: CallEvent[]; cost: number }) {
  const models = useMemo(() => totalsByModel(calls), [calls]);
  const newestFirst = useMemo(() => [...calls].reverse(), [calls]);
  const tokenTotal = useMemo(() => calls.reduce((n, c) => n + c.tokens_in + c.tokens_out, 0), [calls]);

  return (
    <section aria-labelledby="calls-title" className={styles.feed}>
      <div className={styles.head}>
        <h3 id="calls-title" className={shared.label}>
          Model calls
        </h3>
        <p className={styles.total}>
          <span className={styles.sum}>
            <span className="sr-only">Spent so far: </span>
            {usd(cost)}
          </span>
          <span className={styles.sub}>
            {calls.length} {calls.length === 1 ? "call" : "calls"}, {tokens(tokenTotal)} tokens
          </span>
        </p>
      </div>

      {models.length ? (
        <table className={styles.models}>
          <caption className="sr-only">Spend by model</caption>
          <thead className="sr-only">
            <tr>
              <th scope="col">Model and what it did</th>
              <th scope="col">Calls</th>
              <th scope="col">Cost</th>
            </tr>
          </thead>
          <tbody>
            {models.map((m) => (
              <tr key={m.model} style={{ "--share": cost > 0 ? m.cost / cost : 0 } as CSSProperties}>
                <th scope="row">
                  <span className={cx(shared.mono, styles.mname)}>{modelName(m.model)}</span>
                  <span className={styles.roles}>{m.roles.map(roleName).join(", ")}</span>
                </th>
                <td>
                  {m.calls} {m.calls === 1 ? "call" : "calls"}
                </td>
                <td className={styles.mcost}>{usd(m.cost)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      {calls.length ? (
        <ol className={styles.list} tabIndex={0} aria-label="Each call, newest first">
          {newestFirst.map((c, i) => (
            <CallRow key={c.seq ?? `${c.model}-${i}`} call={c} newest={i === 0} />
          ))}
        </ol>
      ) : (
        <p className={styles.empty}>No model calls yet. Each one appears here with its tokens, cost and time.</p>
      )}
    </section>
  );
});
