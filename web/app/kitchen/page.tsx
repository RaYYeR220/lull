import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Footer } from "@/components/chrome/Footer";
import { Nav } from "@/components/chrome/Nav";
import { Player } from "@/components/player/Player";
import { Room } from "@/components/texture/Room";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Door, Gate } from "@/components/ui/Gate";
import { Glyph, type GlyphName } from "@/components/ui/Glyph";
import { Hairline, type HairlineTone } from "@/components/ui/Hairline";
import { MetaList } from "@/components/ui/MetaList";
import { Section } from "@/components/ui/Section";
import { STATUS, StatusMark, type Status } from "@/components/ui/StatusMark";
import { Timecode } from "@/components/ui/Timecode";
import { cx } from "@/lib/cx";
import { MAIN_ID, SHOWCASE_ID, SITE } from "@/lib/site";
import styles from "./kitchen.module.css";

export const metadata: Metadata = {
  title: "Kitchen",
  robots: { index: false, follow: false },
};

type Swatch = { token: string; hex: string; role: string };

const COLOURS: { name: string; swatches: Swatch[] }[] = [
  {
    name: "Night: the room",
    swatches: [
      { token: "night-0", hex: "#0d0f17", role: "Code wells, evidence frames, footer floor" },
      { token: "night-1", hex: "#13151f", role: "Ground; text on lit surfaces" },
      { token: "night-2", hex: "#1a1d2b", role: "Raised surfaces: studio panel, folder" },
      { token: "night-3", hex: "#232739", role: "Lanes and inset fields" },
      { token: "night-4", hex: "#31364c", role: "Inactive meter track, scrollbar thumb" },
      { token: "night-5", hex: "#4a5170", role: "Ruler ticks, non-text marks" },
    ],
  },
  {
    name: "Cream: the ink",
    swatches: [
      { token: "cream-0", hex: "#f6eedf", role: "Headings, primary text, the lit door" },
      { token: "cream-1", hex: "#d6cdbb", role: "Body text and ledes" },
      { token: "cream-2", hex: "#aca491", role: "Captions, labels, timecodes" },
      { token: "cream-3", hex: "#8d8676", role: "Struck-out claims; on night-0 to night-2 only" },
    ],
  },
  {
    name: "Lamp: the only warm light",
    swatches: [
      { token: "lamp-0", hex: "#f6ddb0", role: "Lit text, cue lines, the focus ring" },
      { token: "lamp-1", hex: "#e9cb98", role: "Glows, the voice line, active marks" },
      { token: "lamp-2", hex: "#c9a46a", role: "The lit door's ledge, separators" },
    ],
  },
  {
    name: "Status",
    swatches: [
      { token: "verified", hex: "#a7c8a1", role: "Sage; always with ✓ and a word" },
      { token: "held", hex: "#e7a69d", role: "Madder rose; two bars, a hatch and a word" },
      { token: "edited", hex: "#a8b8e8", role: "Blue pencil; a pen glyph and a word" },
    ],
  },
];

type TypeRole = { role: string; spec: string[]; className: string; sample: ReactNode };

const TYPE: TypeRole[] = [
  { role: "display-1", spec: ["Newsreader 300", "clamp(46px, 6.6vw, 96px)", "line-height .98", "tracking −.042em"], className: "t-display-1", sample: SITE.claim },
  { role: "quote", spec: ["Newsreader italic 300", "clamp(40px, 6.9vw, 104px)", "hanging quote"], className: "t-quote", sample: "“As you can see on this chart…”" },
  { role: "display-2", spec: ["Newsreader 300", "clamp(38px, 4.4vw, 64px)", "line-height 1.02"], className: "t-display-2", sample: "The missing half of a lecture" },
  { role: "proof", spec: ["Newsreader 300", "clamp(30px, 3.9vw, 56px)", "line-height 1.62"], className: "t-proof", sample: "A bald man in a green coat stands by a window, looking out into daylight." },
  { role: "proof, spoken", spec: ["Newsreader italic 300", "line-height 1.12"], className: "t-proof-spoken", sample: "A bald man in a green coat stands by a window." },
  { role: "numeral-xl", spec: ["Newsreader 300", "clamp(64px, 6vw, 88px)"], className: "t-numeral-xl", sample: "$0.02" },
  { role: "numeral-l", spec: ["Newsreader 300", "clamp(38px, 3.4vw, 50px)"], className: "t-numeral-l", sample: "1.2.5" },
  { role: "verb", spec: ["Newsreader 300", "clamp(32px, 2.9vw, 42px)"], className: "t-verb", sample: "Listen" },
  { role: "title", spec: ["Newsreader 300", "26px", "metric names"], className: "t-title", sample: "Overlap with speech" },
  { role: "title, drawer", spec: ["Newsreader 400", "22px"], className: "t-title-s", sample: "Cue 1" },
  { role: "step-index", spec: ["Newsreader italic 300", "26px (22px on phones)", "real sequences only"], className: "t-step-index", sample: "1" },
  { role: "cue", spec: ["Newsreader italic 350", "16px horizon, 16.5px list, 20px drawer"], className: "t-cue", sample: "A bald man stands by a window." },
  { role: "pause-length", spec: ["Newsreader 400", "19px", "tabular"], className: "t-pause", sample: "8.00 s" },
  { role: "wordmark", spec: ["Newsreader italic 300", "34px (30 on phones), 64px footer"], className: "t-wordmark", sample: "lull" },
  { role: "lede", spec: ["Figtree 400", "clamp(17px, 1.32vw, 19px)", "line-height 1.6"], className: "t-lede", sample: SITE.description },
  {
    role: "body",
    spec: ["Figtree 400", "17px (16px on phones)", "line-height 1.65", "34em measure"],
    className: "t-body",
    sample: "Lecturers point. At a chart, an equation, a diagram on the board. A student who can't see the slide gets the pointing and none of the thing.",
  },
  {
    role: "body-s",
    spec: ["Figtree 400", "15.5px", "line-height 1.6"],
    className: "t-body-s",
    sample: "A solver places the lines. Durations are measured from real audio, never guessed by a model.",
  },
  { role: "ui", spec: ["Figtree 500", "15px nav, 17px doors, 14px tabs"], className: "t-ui", sample: "Studio" },
  { role: "small", spec: ["Figtree 400", "13.5px", "captions and eyebrows"], className: "t-small", sample: "The line traces who speaks in 90 seconds of Elephants Dream." },
  { role: "micro", spec: ["Figtree 600", "12.5px", "status words, chips, lane labels"], className: "t-micro", sample: "18 words" },
  { role: "mono", spec: ["IBM Plex Mono 400", "13px", "timecodes, file names, code"], className: "t-mono", sample: "descriptions.vtt" },
  { role: "code", spec: ["IBM Plex Mono 400", "14px / 1.75 (12px on phones)"], className: "t-code", sample: "lull describe lecture.mp4 --out described/" },
];

const SPACE = [
  ["s-1", "4px"],
  ["s-2", "8px"],
  ["s-3", "12px"],
  ["s-4", "16px"],
  ["s-5", "24px"],
  ["s-6", "32px"],
  ["s-7", "48px"],
  ["s-8", "64px"],
  ["s-9", "96px"],
  ["s-10", "128px"],
  ["s-11", "176px"],
] as const;

const HAIRLINES: { tone: HairlineTone; use: string }[] = [
  { tone: "faint", use: "6%: rows inside panels" },
  { tone: "default", use: "10%: the default rule" },
  { tone: "strong", use: "18%: panel and chip borders" },
  { tone: "door", use: "28%: the dark door" },
  { tone: "lamp", use: "Deep lamp: the measured rule" },
];

const MOTION = [
  ["--ease-out", "cubic-bezier(.16, 1, .3, 1)", "Entrances, presses, the strike"],
  ["--ease-breathe", "cubic-bezier(.45, 0, .55, 1)", "The lamp's breathing"],
  ["--t-enter", ".8s", "The load entrance, staggered .08, .2, .32, .42s"],
  ["--t-hover", ".35s", "Colour, border and glow on hover"],
  ["--t-press", ".12s", "Doors drop 2px; the ledge collapses"],
  ["--t-breathe", "30s", "One lamp breath; paused by body.is-still"],
] as const;

/** The recorded run's files, where the showcase export puts them. Missing until it runs. */
const MEDIA = `/showcase/${SHOWCASE_ID}`;

const STATUSES = Object.keys(STATUS) as Status[];
const GLYPHS: GlyphName[] = ["ok", "out", "held", "edit", "approved", "extended", "play", "pause", "replay"];

export default function Kitchen() {
  return (
    <>
      <Nav />
      <main id={MAIN_ID} tabIndex={-1} className={styles.main}>
        <header className={cx("wrap", styles.intro)}>
          <Eyebrow>Design system</Eyebrow>
          <h1 className="t-display-1">Kitchen</h1>
          <p className="t-lede">Every token and primitive of the Nocturne system, drawn with the same CSS the site ships. Not linked from anywhere.</p>
        </header>

        <Section id="colour" eyebrow="Tokens" heading="Colour" lede="One lamp in a blue-black room. Status colours always travel with a glyph and a word." ruled>
          {COLOURS.map((group) => (
            <div key={group.name} className={styles.group}>
              <h3 className={cx("t-title", styles.groupTitle)}>{group.name}</h3>
              <ul className={styles.swatches}>
                {group.swatches.map((s) => (
                  <li key={s.token} className={styles.swatch}>
                    <span className={styles.chip} style={{ background: `var(--${s.token})` }} aria-hidden="true" />
                    <span className={styles.swatchText}>
                      <span className={cx("t-mono", styles.token)}>{s.token}</span>
                      <span className={cx("t-mono", styles.hex)}>{s.hex}</span>
                      <span className={cx("t-small", styles.role)}>{s.role}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
          <div className={styles.group}>
            <h3 className={cx("t-title", styles.groupTitle)}>Hairlines</h3>
            <ul className={styles.hairlines}>
              {HAIRLINES.map((h) => (
                <li key={h.tone}>
                  <span className={cx("t-small", styles.hairLabel)}>
                    <span className="t-mono">{h.tone}</span> {h.use}
                  </span>
                  <Hairline tone={h.tone} />
                </li>
              ))}
            </ul>
          </div>
        </Section>

        <Section id="type" eyebrow="Tokens" heading="Type" lede="Newsreader for display; its italic is a voice, never emphasis. Figtree for reading and controls. Plex Mono for timecodes, files and code.">
          <ul className={styles.typeList}>
            {TYPE.map((t) => (
              <li key={t.role} className={styles.typeRow}>
                <div className={styles.typeMeta}>
                  <span className={cx("t-micro", styles.typeRole)}>{t.role}</span>
                  <MetaList items={t.spec} className={styles.typeSpec} />
                </div>
                <p className={cx(t.className, styles.typeSample)}>{t.sample}</p>
              </li>
            ))}
          </ul>
        </Section>

        <Section id="space" eyebrow="Tokens" heading="Space" lede="Eleven steps from 4px to 176px. A 12-column grid with a 24px gap, max 1440px, radius 0 everywhere." ruled>
          <ul className={styles.space}>
            {SPACE.map(([token, px]) => (
              <li key={token}>
                <span className={cx("t-mono", styles.spaceToken)}>{token}</span>
                <span className={styles.spaceBar} style={{ width: `var(--${token})` }} aria-hidden="true" />
                <span className={cx("t-small", styles.spacePx)}>{px}</span>
              </li>
            ))}
          </ul>
        </Section>

        <Section id="texture" eyebrow="Tokens" heading="Texture" lede="Grain over the whole page, light pools in the room, a ruled floor that alternates by section.">
          <div className={styles.roomDemo}>
            <Room />
            <p className={cx("t-small", styles.roomNote)}>The hero room: lamp and cone breathing on a 30 s loop, two cool pools, a vignette, the 24px floor.</p>
          </div>
          <div className={cx("ruled", styles.ruledDemo)}>
            <p className="t-small">The ruled floor: one 2.8% cream hairline every 40px.</p>
          </div>
        </Section>

        <Section id="primitives" eyebrow="Components" heading="Primitives" lede="The parts every page is built from. This page itself is a stack of Section." ruled>
          <div className={styles.prims}>
            <article className={styles.prim}>
              <h3 className="t-title">Gate</h3>
              <p className="t-body-s">One lit door, one dark door. Hover brightens the glow; pressing drops the door 2px and collapses the ledge.</p>
              <Gate label="Choose how to begin">
                <Door tone="lit" icon={<Glyph name="play" size={14} />}>
                  Preview the timing
                </Door>
                <Door tone="dark" href="/studio">
                  Open the studio
                </Door>
              </Gate>
            </article>

            <article className={styles.prim}>
              <h3 className="t-title">StatusMark</h3>
              <p className="t-body-s">A glyph, a word and a colour. Never colour alone.</p>
              <ul className={styles.marks}>
                {STATUSES.map((s) => (
                  <li key={s}>
                    <StatusMark status={s} />
                    <StatusMark status={s} variant="pill" />
                  </li>
                ))}
                <li>
                  <StatusMark status="verified" label="Seen" />
                  <span className={styles.heldBlock} aria-hidden="true">
                    <Glyph name="held" size={11} />
                  </span>
                  <span className="t-small">Held cue block: the hatch fill</span>
                </li>
              </ul>
            </article>

            <article className={styles.prim}>
              <h3 className="t-title">Eyebrow</h3>
              <p className="t-body-s">Sentence case, a 24px lamp rule before it.</p>
              <Eyebrow>For developers</Eyebrow>
            </article>

            <article className={styles.prim}>
              <h3 className="t-title">Timecode</h3>
              <p className="t-body-s">Mono and tabular, with a machine-readable duration.</p>
              <ul className={styles.timecodes}>
                <li>
                  <Timecode seconds={9.25} />
                </li>
                <li>
                  <Timecode seconds={71.04} />
                </li>
                <li>
                  <Timecode seconds={59.999} />
                </li>
                <li>
                  <Timecode seconds={90} digits={0} />
                </li>
              </ul>
            </article>

            <article className={styles.prim}>
              <h3 className="t-title">MetaList</h3>
              <p className="t-body-s">Short facts separated by 3px deep-lamp squares, not middle dots.</p>
              <MetaList
                label="What Lull guarantees"
                items={[
                  <span key="overlap">
                    <b>0</b> overlaps with speech
                  </span>,
                  "every line checked against its frames",
                  "NVIDIA Nemotron on Nebius",
                ]}
              />
            </article>

            <article className={styles.prim}>
              <h3 className="t-title">Glyph</h3>
              <p className="t-body-s">A 12-unit grid in currentColor. Decorative; the word beside it carries the meaning.</p>
              <ul className={styles.glyphs}>
                {GLYPHS.map((g) => (
                  <li key={g}>
                    <Glyph name={g} size={16} />
                    <span className="t-mono">{g}</span>
                  </li>
                ))}
              </ul>
            </article>
          </div>
        </Section>

        <Section
          id="player"
          eyebrow="Components"
          heading="Player"
          lede="The described-video player, as the showcase and embeds use it. Until the recorded run's media is exported it says the video isn't there and why."
        >
          <div className={styles.player}>
            <Player
              src={`${MEDIA}/video.mp4`}
              descriptions={`${MEDIA}/descriptions.vtt`}
              extended={`${MEDIA}/extended.vtt`}
              describedAudio={`${MEDIA}/described-audio.m4a`}
              cueAudioBase={`${MEDIA}/audio/`}
              label="Elephants Dream, described"
            />
          </div>
        </Section>

        <Section id="motion" eyebrow="Tokens" heading="Motion" lede="One orchestrated entrance, one breathing lamp. Reduced motion turns both off and nothing waits on opacity 0." ruled>
          <table className={styles.table}>
            <caption className="sr-only">Motion tokens</caption>
            <thead>
              <tr>
                <th scope="col">Token</th>
                <th scope="col">Value</th>
                <th scope="col">Used for</th>
              </tr>
            </thead>
            <tbody>
              {MOTION.map(([token, value, use]) => (
                <tr key={token}>
                  <th scope="row" className="t-mono">
                    {token}
                  </th>
                  <td className="t-mono">{value}</td>
                  <td>{use}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className={cx("t-small", styles.focusNote)}>Tab through this page: every link and control shows the warm 2px focus ring.</p>
        </Section>
      </main>
      <Footer />
    </>
  );
}
