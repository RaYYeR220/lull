import type { Metadata } from "next";
import { Footer } from "@/components/chrome/Footer";
import { Nav } from "@/components/chrome/Nav";
import { EmbedDemo } from "@/components/developers/EmbedDemo";
import { Endpoint } from "@/components/developers/Endpoint";
import { DevTabs } from "@/components/landing/DevTabs";
import { CopyCode } from "@/components/ui/CopyCode";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { API_BASE } from "@/lib/api";
import { API_REFERENCE, REFERENCE_ORDER, REFUSAL_SHAPE, type EndpointDoc } from "@/lib/api-reference";
import { cx } from "@/lib/cx";
import { MCP_AVAILABLE } from "@/lib/features";
import { MODELS } from "@/lib/models";
import { ATTRIBUTE_DOCS } from "@/lib/player-reference";
import { MAIN_ID, REPO_URL } from "@/lib/site";
import { KEYS, MODES, MODE_DESC, MODE_NAME, PLAYER_ATTRIBUTES } from "@/player/src/reference";
import styles from "@/components/developers/Developers.module.css";

export const metadata: Metadata = {
  title: "Developers",
  description: "Call Lull from a terminal or embed its player: the REST API with curl, the command line, the player's attributes, and every endpoint with its answers.",
};

const GROUPS: EndpointDoc["group"][] = ["Live runs", "Reading runs", "Studio"];

const REST_STEPS: { title: string; what: string; code: string; label: string; answer?: string }[] = [
  {
    title: "Point your shell at the API",
    what: "Every call below uses this address.",
    code: `export LULL_API="${API_BASE}"`,
    label: "Set the API address",
  },
  {
    title: "Check it can take a run",
    what: "ok and speech true, live_busy false, and the day’s spend under its cap.",
    code: `curl -s "$LULL_API/v1/health"`,
    label: "Health",
    answer: API_REFERENCE.health.success.shape,
  },
  {
    title: "See what it can describe",
    what: "Sample clips of up to 45 s, each with its title, length, credit and licence. Or send your own file instead, up to 60 s and 30 MB; longer clips are refused (too_long).",
    code: `curl -s "$LULL_API/v1/samples"`,
    label: "Samples",
    answer: API_REFERENCE.samples.success.shape,
  },
  {
    title: "Describe a sample, and watch it happen",
    what: "-N keeps curl from buffering: each stage, model call and line arrives as it happens. The first event has the run id and its edit token; keep both.",
    code: `curl -N "$LULL_API/v1/runs" -F sample=ed45 -F mode=standard -F voice=Sofia

# or your own clip
curl -N "$LULL_API/v1/runs" -F file=@lecture.mp4 -F mode=standard`,
    label: "Start a run",
    answer: API_REFERENCE.startRun.success.shape,
  },
  {
    title: "Fetch what it published",
    what: "The run's manifest and cues, then any of its files.",
    code: `RUN=live-…   # the run_id from the first event
curl -s "$LULL_API/v1/runs/$RUN"
curl -sO "$LULL_API/v1/runs/$RUN/files/descriptions.vtt"
curl -sO "$LULL_API/v1/runs/$RUN/files/described-audio.m4a"`,
    label: "Fetch the outputs",
  },
];

const CLI_OPTIONS: [string, string][] = [
  ["--out DIR", "Output folder (default runs/<run id> under the current folder)."],
  ["--mode standard|extended", "extended: the plan writes long versions for the extended track. Both modes build extended.mp4 when an important visual fits no pause."],
  ["--profile education|entertainment", "What matters most: slides, charts and on-screen text, or action, people and setting."],
  ["--voice NAME", "The Magpie TTS voice (default Sofia)."],
  ["--source-url URL", "A page about the video, read for names and terms."],
  ["--no-research", "Don’t look names and terms up on the web."],
  ["--run-id ID", "Run id for the spend ledger and the cache (default <video stem>-<sha256[:8]>)."],
];

const CLI_ENV: [string, string, string][] = [
  ["NEBIUS_API_KEY", "required", "Nebius Token Factory, for the model calls."],
  ["TAVILY_API_KEY", "optional", "Web lookups of names and terms; without it research is skipped."],
  ["LULL_SPEECH_URL", "http://127.0.0.1:8089", "The NeMo-Speech.cpp server: transcription, speakers and the voice."],
  ["LULL_SPEECH_KEY", "none", "That server’s key, if it has one."],
  ["LULL_HOME", "~/.lull", "Where the spend ledger and the voices’ measured rates live."],
  ["LULL_CAP_RUN", "0.50", "USD of model calls one run may spend."],
  ["LULL_CAP_DAY", "3.00", "USD of model calls per UTC day."],
  ["LULL_CAP_TOTAL", "35.00", "USD of model calls in all."],
  ["LULL_TAVILY_RUN", "5", "Web searches one run may make."],
  ["LULL_TAVILY_DAY", "60", "Web searches per UTC day."],
];

const EXIT_CODES: [string, string][] = [
  ["0", "published"],
  ["1", "an error (one line on the console; the details in the run’s run.log)"],
  ["2", "bad usage"],
  ["3", "stopped by a spend cap or the provider’s balance (finished stages are cached: the same command resumes)"],
  ["4", "nothing published: a line would overlap speech"],
];

const OUTPUTS: [string, string][] = [
  ["descriptions.vtt", "the voiced standard lines, as a WebVTT descriptions track"],
  ["extended.vtt", "the extended lines at their freeze points, each after NOTE extended pause=<s>"],
  ["described-audio.m4a", "the soundtrack ducked under the lines"],
  ["described.mp4", "the video with a Described track first and the Original second"],
  ["extended.mp4", "the extended version, when an extended line is voiced"],
  ["cues.json", "every line, held ones included, with its checks"],
  ["script.md", "the description script"],
  ["report.html", "the conformance report: WCAG status, coverage, held lines, spend"],
  ["manifest.json", "the sha256 and size of every file, the run’s summary and versions"],
];

function RestPanel() {
  return (
    <ol className={styles.steps}>
      {REST_STEPS.map((s) => (
        <li key={s.title} className={styles.step}>
          <h3 className={styles.h3}>{s.title}</h3>
          <p className={styles.stepWhat}>{s.what}</p>
          <CopyCode code={s.code} label={s.label} lang="sh" />
          {s.answer ? (
            <details className={styles.shape}>
              <summary>What comes back</summary>
              <CopyCode code={s.answer} label={`${s.label} answer`} />
            </details>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

function CliPanel() {
  return (
    <div className={styles.cli}>
      <div>
        <h3 className={styles.h3}>Install and describe</h3>
        <p className={styles.stepWhat}>
          The engine is the <span className={styles.mono}>engine/</span> folder of the{" "}
          <a href={REPO_URL} className={styles.link}>
            repository
          </a>
          . It needs ffmpeg, a Token Factory key and a NeMo-Speech.cpp server (<span className={styles.mono}>nemo-speech serve</span>).
        </p>
        <CopyCode
          code={`uv tool install ./engine
export NEBIUS_API_KEY=…
lull describe lecture.mp4 --out described/ --mode standard --profile education`}
          label="Install and describe a video"
          lang="sh"
        />
      </div>
      <table className={styles.table}>
        <caption className={styles.caption}>lull describe VIDEO, options</caption>
        <tbody>
          {CLI_OPTIONS.map(([o, what]) => (
            <tr key={o}>
              <th scope="row" className={styles.mono}>
                {o}
              </th>
              <td>{what}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <table className={styles.table}>
        <caption className={styles.caption}>What it writes</caption>
        <tbody>
          {OUTPUTS.map(([f, what]) => (
            <tr key={f}>
              <th scope="row" className={styles.mono}>
                {f}
              </th>
              <td>{what}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <table className={styles.table}>
        <caption className={styles.caption}>Exit codes</caption>
        <tbody>
          {EXIT_CODES.map(([c, what]) => (
            <tr key={c}>
              <th scope="row" className={styles.mono}>
                {c}
              </th>
              <td>{what}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div>
        <h3 className={styles.h3}>The other commands</h3>
        <CopyCode
          code={`lull calibrate-voice --voice Sofia      # measure a voice's words per second
lull ledger                             # spend today and in all, against the caps
lull ledger --run lecture-1a2b3c4d      # one run's calls by role and model
lull export-showcase runs/lecture-1a2b3c4d site/ --id lecture --title "Lecture 3" --credit "…"`}
          label="Other commands"
          lang="sh"
        />
      </div>
      <table className={styles.table}>
        <caption className={styles.caption}>Settings, from the environment or a .env file</caption>
        <thead>
          <tr>
            <th scope="col">Variable and default</th>
            <th scope="col">What</th>
          </tr>
        </thead>
        <tbody>
          {CLI_ENV.map(([v, d, what]) => (
            <tr key={v}>
              <th scope="row">
                <span className={styles.mono}>{v}</span>
                <span className={styles.sub}>{d}</span>
              </th>
              <td>{what}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className={styles.stepWhat}>A cap of 0 refuses every call; “none” switches a cap off.</p>
    </div>
  );
}

function EmbedPanel() {
  return (
    <div className={styles.cli}>
      <div>
        <h3 className={styles.h3}>One script, one tag</h3>
        <p className={styles.stepWhat}>
          The player is a web component with no dependencies (about 20 kB gzipped). Paste the snippet into any page: the player and the recorded
          runs’ files are served to any site. For a run of your own, point it at copies of its files on your site.
        </p>
      </div>
      <EmbedDemo />
      <table className={styles.table}>
        <caption className={styles.caption}>Attributes</caption>
        <thead>
          <tr>
            <th scope="col">Attribute and value</th>
            <th scope="col">What</th>
          </tr>
        </thead>
        <tbody>
          {PLAYER_ATTRIBUTES.map((a) => (
            <tr key={a} data-attribute={a}>
              <th scope="row">
                <span className={styles.mono}>{a}</span>
                <span className={styles.sub}>{ATTRIBUTE_DOCS[a].values}</span>
              </th>
              <td>{ATTRIBUTE_DOCS[a].what}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <table className={styles.table}>
        <caption className={styles.caption}>Modes</caption>
        <tbody>
          {MODES.map((m) => (
            <tr key={m}>
              <th scope="row">
                {MODE_NAME[m]} <span className={styles.mono}>{m}</span>
              </th>
              <td>{MODE_DESC[m]}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <table className={styles.table}>
        <caption className={styles.caption}>Keys, while the focus is in the player</caption>
        <tbody>
          {KEYS.map(([keys, what]) => (
            <tr key={what}>
              <th scope="row">
                {keys.map((k) => (
                  <kbd key={k} className={styles.kbd}>
                    {k}
                  </kbd>
                ))}
              </th>
              <td>{what}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div>
        <h3 className={styles.h3}>From script</h3>
        <CopyCode
          code={`const player = document.querySelector("lull-player");
player.mode = "spoken";            // mixed, spoken, screen-reader, extended
player.play(); player.pause(); player.seek(42);
player.state;                      // { mode, playing, holding, activeCue, … }

player.addEventListener("lull-cue", (e) => console.log(e.detail));
// { id, text, start, kind: "standard" | "extended", mode, via, hold? }
player.addEventListener("lull-modechange", (e) => console.log(e.detail.mode));`}
          label="Properties and events"
          lang="js"
        />
      </div>
    </div>
  );
}

/**
 * The integrator's page: call the API from a terminal in a minute, run the engine yourself,
 * embed the player with one tag, and every endpoint with what it answers and how it refuses.
 */
export default function DevelopersPage() {
  return (
    <>
      <Nav />
      <main id={MAIN_ID} tabIndex={-1} className={styles.main}>
        <div className="wrap">
          <header className={styles.head}>
            <Eyebrow className={styles.eyebrow}>Developers</Eyebrow>
            <h1 className={cx("t-display-2", styles.title)}>Call Lull from a terminal, or play its files on your page.</h1>
            <p className={cx("t-lede", styles.lede)}>
              The same pipeline as the studio, with the same checks: a REST API that streams each run as it happens, the command line it runs
              on, and an accessible player that takes the files it writes.
            </p>
            <p className={styles.base}>
              Live API <span className={styles.mono}>{API_BASE}</span>
            </p>
            <nav aria-label="On this page" className={styles.toc}>
              <a href="#quick-start">Quick start</a>
              <a href="#endpoints">Endpoints</a>
              <a href="#models">Models</a>
              {MCP_AVAILABLE ? <a href="#mcp">MCP</a> : null}
            </nav>
          </header>

          <section id="quick-start" aria-labelledby="quick-start-title" className={styles.section}>
            <h2 id="quick-start-title" className={styles.h2}>
              Quick start
            </h2>
            <div className={styles.term}>
              <DevTabs
                label="Ways to use Lull"
                tabs={[
                  { id: "rest", label: "REST", panel: <RestPanel /> },
                  { id: "cli", label: "CLI", panel: <CliPanel /> },
                  { id: "embed", label: "Embed", panel: <EmbedPanel /> },
                ]}
              />
            </div>
          </section>

          <section id="endpoints" aria-labelledby="endpoints-title" className={styles.section}>
            <h2 id="endpoints-title" className={styles.h2}>
              Endpoints
            </h2>
            <p className={styles.sectionLede}>
              JSON in and out, except the run streams (text/event-stream) and the files. A refusal is JSON with the API’s own code and plain
              words, plus the code’s extras (cue_ids, findings, scope); some 409, 429 and 503 refusals send Retry-After in seconds.
            </p>
            <CopyCode code={REFUSAL_SHAPE} label="Shape of a refusal" />
            {GROUPS.map((g) => (
              <section key={g} aria-labelledby={`group-${g}`} className={styles.group}>
                <h3 id={`group-${g}`} className={styles.groupTitle}>
                  {g}
                </h3>
                {REFERENCE_ORDER.filter((n) => API_REFERENCE[n].group === g).map((n) => (
                  <Endpoint key={n} name={n} doc={API_REFERENCE[n]} />
                ))}
              </section>
            ))}
          </section>

          <section id="models" aria-labelledby="models-title" className={styles.section}>
            <h2 id="models-title" className={styles.h2}>
              Models
            </h2>
            <p className={styles.sectionLede}>
              Which NVIDIA model does what in a run. The language models run on Nebius Token Factory and are called by id; speech runs on a
              NeMo-Speech.cpp server.
            </p>
            <table className={cx(styles.table, styles.models)}>
              <thead>
                <tr>
                  <th scope="col">Model</th>
                  <th scope="col">What it does</th>
                </tr>
              </thead>
              <tbody>
                {MODELS.map((m) => (
                  <tr key={`${m.stage}-${m.model}`}>
                    <th scope="row">
                      {m.model}
                      <span className={styles.sub}>{m.stage}</span>
                      <span className={styles.modelId}>{m.id ?? `on the ${m.where}`}</span>
                    </th>
                    <td>
                      {m.does}
                      <span className={styles.sub}>Licence: {m.licence ?? "see its model card on Token Factory"}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          {MCP_AVAILABLE ? (
            <section id="mcp" aria-labelledby="mcp-title" className={styles.section}>
              <h2 id="mcp-title" className={styles.h2}>
                MCP
              </h2>
            </section>
          ) : null}
        </div>
      </main>
      <Footer />
    </>
  );
}
