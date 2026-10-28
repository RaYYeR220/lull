import Link from "next/link";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { cx } from "@/lib/cx";
import { MCP_AVAILABLE } from "@/lib/features";
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
          <span className={styles.note}>{"# streams progress; the first event carries the run id.\n# GET /v1/runs/{id} then lists its nine files:\n#   descriptions.vtt  extended.vtt  described-audio.m4a\n#   described.mp4  extended.mp4 (when made)  cues.json\n#   script.md  report.html  manifest.json"}</span>
        </pre>
        <p className={styles.after}>Every endpoint, with its answers, in the developer docs.</p>
      </>
    ),
  },
  {
    id: "embed",
    label: "Player",
    panel: (
      <>
        <pre tabIndex={0}>
          <span className={styles.note}>{"<!-- one script, one tag -->"}</span>
          {"\n<"}
          <span className={styles.cmd}>script</span> <span className={styles.flag}>type</span>=<span className={styles.str}>&quot;module&quot;</span>{" "}
          <span className={styles.flag}>src</span>=<span className={styles.str}>&quot;/player.js&quot;</span>
          {"></"}
          <span className={styles.cmd}>script</span>
          {">\n<"}
          <span className={styles.cmd}>lull-player</span>
          {"\n  "}
          <span className={styles.flag}>src</span>=<span className={styles.str}>&quot;lecture.mp4&quot;</span>
          {"\n  "}
          <span className={styles.flag}>descriptions</span>=<span className={styles.str}>&quot;descriptions.vtt&quot;</span>
          {"\n  "}
          <span className={styles.flag}>described-audio</span>=<span className={styles.str}>&quot;described-audio.m4a&quot;</span>
          {"></"}
          <span className={styles.cmd}>lull-player</span>
          {">"}
        </pre>
        <p className={styles.after}>Mixed audio, spoken lines, your screen reader, or the extended version: the viewer chooses.</p>
      </>
    ),
  },
];

/** 8. For developers: the same pipeline from a terminal, an API or a page (and an agent, once MCP ships). */
export function Developers() {
  return (
    <section id="developers" aria-labelledby="developers-title" className={cx(styles.sec, "ruled")}>
      <div className={cx("wrap", styles.grid)}>
        <div className={styles.copy}>
          <Eyebrow>For developers</Eyebrow>
          <h2 id="developers-title" className="t-display-2">
            {MCP_AVAILABLE ? "From a terminal, an API or an agent." : "From a terminal, an API or a web page."}
          </h2>
          <p className={cx("t-lede", styles.lede)}>The same pipeline as the studio, with the same checks and the same files.</p>
          {MCP_AVAILABLE ? (
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
          ) : (
            <ul className={styles.tools} aria-label="Where to start">
              <li>
                <span className="t-mono">POST /v1/runs</span>
                <span>describe a clip, streamed</span>
              </li>
              <li>
                <span className="t-mono">GET /v1/runs/{"{id}"}</span>
                <span>its files and checks</span>
              </li>
              <li>
                <span className="t-mono">&lt;lull-player&gt;</span>
                <span>play them on your page</span>
              </li>
            </ul>
          )}
          <p className={styles.docs}>
            <Link href="/developers" className={styles.docsLink}>
              Read the developer docs
            </Link>
          </p>
        </div>
        <div className={styles.term}>
          <DevTabs label="Ways to call Lull" tabs={TABS} />
        </div>
      </div>
    </section>
  );
}
