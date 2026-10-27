import { Eyebrow } from "@/components/ui/Eyebrow";
import { cx } from "@/lib/cx";
import { DevTabs, type DevTab } from "./DevTabs";
import styles from "./Developers.module.css";

const OUTPUTS = ["descriptions.vtt", "described-audio.m4a", "described.mp4", "extended.mp4", "report.html", "script.md"];

const TABS: DevTab[] = [
  {
    id: "cli",
    label: "CLI",
    panel: (
      <>
        <pre tabIndex={0}>
          <span className={styles.prompt}>$ </span>
          <span className={styles.cmd}>lull describe lecture.mp4 --out described/</span>
          {"\n\n"}
          <span className={styles.note}># writes</span>
          {`\ndescribed/\n${OUTPUTS.map((f) => `  ${f}`).join("\n")}`}
        </pre>
        <p className={styles.after}>Runs the whole pipeline: listen, watch, write, fit, check and speak.</p>
      </>
    ),
  },
  {
    id: "rest",
    label: "REST",
    panel: (
      <>
        <pre tabIndex={0}>
          <span className={styles.prompt}>$ </span>
          <span className={styles.cmd}>curl -N -X POST</span> <span className={styles.str}>&quot;$LULL_API/v1/runs&quot;</span>
          {" \\\n    "}
          <span className={styles.flag}>-F</span> <span className={styles.str}>&quot;file=@lecture.mp4&quot;</span>
          {"\n\n"}
          <span className={styles.note}>{"# streams progress; the first event carries the run id,\n# then GET /v1/runs/{id} lists the six files"}</span>
        </pre>
        <p className={styles.after}>OpenAPI reference in the docs.</p>
      </>
    ),
  },
  {
    id: "mcp",
    label: "MCP",
    panel: (
      <>
        <pre tabIndex={0}>
          {"{\n  "}
          <span className={styles.str}>&quot;tool&quot;</span>: <span className={styles.str}>&quot;describe_video&quot;</span>
          {",\n  "}
          <span className={styles.str}>&quot;arguments&quot;</span>
          {": {\n    "}
          <span className={styles.str}>&quot;url&quot;</span>: <span className={styles.str}>&quot;https://example.edu/lectures/week-3.mp4&quot;</span>
          {"\n  }\n}"}
        </pre>
        <p className={styles.after}>
          Then <span className="t-mono">get_tracks</span> for the files and <span className="t-mono">review_queue</span> for anything held.
        </p>
      </>
    ),
  },
];

/** 8. For developers: the same pipeline from a terminal, an API or an agent. */
export function Developers() {
  return (
    <section id="developers" aria-labelledby="developers-title" className={cx(styles.sec, "ruled")}>
      <div className={cx("wrap", styles.grid)}>
        <div className={styles.copy}>
          <Eyebrow>For developers</Eyebrow>
          <h2 id="developers-title" className="t-display-2">
            From a terminal, an API or an agent.
          </h2>
          <p className={cx("t-lede", styles.lede)}>The same pipeline as the studio, with the same checks and the same six files.</p>
          <ul className={styles.tools} aria-label="MCP tools">
            <li>
              <span className="t-mono">describe_video</span>
              <span>start a run</span>
            </li>
            <li>
              <span className="t-mono">get_tracks</span>
              <span>fetch the outputs</span>
            </li>
            <li>
              <span className="t-mono">review_queue</span>
              <span>lines waiting for a person</span>
            </li>
          </ul>
        </div>
        <div className={styles.term}>
          <DevTabs label="Ways to call Lull" tabs={TABS} />
        </div>
      </div>
    </section>
  );
}
