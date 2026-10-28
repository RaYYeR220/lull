import { CopyCode } from "@/components/ui/CopyCode";
import type { EndpointDoc, Field } from "@/lib/api-reference";
import { ROUTES, type RouteName } from "@/lib/api-routes";
import styles from "./Developers.module.css";

const AUTH: Record<EndpointDoc["auth"], string> = {
  none: "None.",
  "edit-token": "Authorization: Bearer <the run’s edit token> (from its run event, or from a fork).",
  "edit-token to resume": "None to read; to resume a run that stopped before it finished, Authorization: Bearer <its edit token>.",
  "edit-token to see an unfinished run": "None for a done run; for the rest of a run that isn't done, Authorization: Bearer <its edit token>.",
};

function Fields({ fields, caption }: { fields: Field[]; caption: string }) {
  return (
    <table className={styles.table}>
      <caption className={styles.caption}>{caption}</caption>
      <thead>
        <tr>
          <th scope="col">Name and type</th>
          <th scope="col">What</th>
        </tr>
      </thead>
      <tbody>
        {fields.map((f) => (
          <tr key={f.name}>
            <th scope="row">
              <span className={styles.mono}>{f.name}</span>
              {f.required ? "" : <span className={styles.optional}> optional</span>}
              <span className={styles.sub}>{f.type}</span>
            </th>
            <td>{f.what}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** One route of the API: what it does, what it takes, what comes back, and how it says no. */
export function Endpoint({ name, doc }: { name: RouteName; doc: EndpointDoc }) {
  const route = ROUTES[name];
  const id = `ep-${name}`;
  return (
    <article className={styles.endpoint} aria-labelledby={id} data-endpoint={name}>
      <h4 id={id} className={styles.epHead}>
        <span className={styles.method} data-method={route.method}>
          {route.method}
        </span>
        <span className={styles.path}>{route.path}</span>
      </h4>
      <p className={styles.epSummary}>{doc.summary}</p>
      <dl className={styles.epFacts}>
        <div>
          <dt>Auth</dt>
          <dd>{AUTH[doc.auth]}</dd>
        </div>
        {doc.limits ? (
          <div>
            <dt>Limits</dt>
            <dd>{doc.limits}</dd>
          </div>
        ) : null}
      </dl>
      {doc.params ? <Fields fields={doc.params} caption="Parameters" /> : null}
      {doc.body ? <Fields fields={doc.body.fields} caption={`Body, ${doc.body.type}`} /> : null}
      <div className={styles.answer}>
        <p className={styles.answerHead}>
          <span className={styles.mono}>{doc.success.status}</span> {doc.success.type}. {doc.success.what}
        </p>
        <CopyCode code={doc.success.shape} label={`Shape of the ${route.method} ${route.path} answer`} />
      </div>
      {doc.refusals.length ? (
        <table className={styles.table}>
          <caption className={styles.caption}>Refusals</caption>
          <thead>
            <tr>
              <th scope="col">Status</th>
              <th scope="col">error</th>
              <th scope="col">When</th>
            </tr>
          </thead>
          <tbody>
            {doc.refusals.map((r) => (
              <tr key={`${r.status}-${r.code}-${r.when}`}>
                <td className={styles.mono}>{r.status}</td>
                <th scope="row" className={styles.mono}>
                  {r.code}
                </th>
                <td>
                  {r.when}
                  {r.retryAfter ? <span className={styles.retry}> Sends Retry-After.</span> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <CopyCode code={doc.example} label={`curl: ${route.method} ${route.path}`} lang="sh" />
    </article>
  );
}
