import type { ReactNode } from "react";
import { Section } from "@/components/ui/Section";
import { cx } from "@/lib/cx";
import type { Showcase } from "@/lib/showcase";
import { closeUp, GUARD_S, numberWord, speechOverlap, voicedCues, type CloseUp, type PlacedCue } from "@/lib/showcase-derive";
import { formatTimecode as fmt } from "@/lib/timecode";
import styles from "./Guarantee.module.css";

const LAMP = "233,203,152";
const CREAM = "246,238,223";
/** Room the placed line's own text needs: 12.5px semibold Figtree, two quote marks and the inset. */
const textPx = (text: string) => Math.ceil((text.length + 2) * 7 + 20);

const P = (x: number) => `${x.toFixed(3)}%`;

type FigureProps = { narrow: boolean; showcase: Showcase; cu: CloseUp; voiced: PlacedCue[]; overlap: number };

/**
 * The figure, drawn at the real width of the page: x positions are percentages of the
 * figure, everything else (text, ticks, insets) stays in pixels, so the type never scales.
 */
function Figure({ narrow, showcase, cu, voiced, overlap }: FigureProps) {
  const T = showcase.duration;
  const { gap, cue, a, b } = cu;
  const id = narrow ? "gn" : "gw";
  const ox = (t: number) => (t / T) * 100;
  const dx = (t: number) => ((t - a) / (b - a)) * 100;

  // the whole clip
  const OY = 16;
  const D0 = narrow ? 150 : 160;
  const height = D0 + (narrow ? 250 : 236);
  const sy = OY + 8;
  const wy = OY + 32;
  const ay = wy + 30;
  const ticks: ReactNode[] = [];
  for (let t = 0; t <= T; t += 10) {
    const shift = t === T ? "translate(-1 0)" : undefined;
    if (narrow && t % 30) {
      ticks.push(<rect key={t} x={P(ox(t))} y={ay} width="1" height="3" fill={`rgba(${CREAM},.3)`} transform={shift} />);
      continue;
    }
    ticks.push(
      <g key={t}>
        <rect x={P(ox(t))} y={ay} width="1" height="5" fill={`rgba(${CREAM},.4)`} transform={shift} />
        <text className={styles.mono} x={P(ox(t))} y={ay + 18} textAnchor={t === 0 ? "start" : t === T ? "end" : "middle"} fill="#aca491">
          {fmt(t, 0)}
        </text>
      </g>,
    );
  }
  const lx0 = ox(a);
  const lx1 = ox(b);
  const ly0 = sy - 6;
  const ly1 = wy + 24;
  const br = 6;

  // the close-up
  const top = D0;
  const spY = D0 + 46;
  const spH = 46;
  const wY = D0 + 108;
  const wH = narrow ? 118 : 88;
  const axY = wY + wH + 18;
  const gx0 = dx(gap.start);
  const gx1 = dx(gap.end);
  const edgeY = narrow ? spY - 4 : top + 18;
  const tcY = narrow ? wY + wH - 9 : top + 28; // narrow: timecodes sit on the window floor, clear of the word labels
  const cx0 = dx(cue.start);
  const cx1 = dx(cue.end);
  const cy = wY + (narrow ? 42 : 32);
  const gy = cy + 30;
  const wLen = (gap.end - gap.start).toFixed(2);
  // a guard is drawn only where the pause really is one guard from speech
  const guards: [number, number][] = [
    ...(cu.guardBefore ? [[gap.start - GUARD_S, gap.start] as [number, number]] : []),
    ...(cu.guardAfter ? [[gap.end, gap.end + GUARD_S] as [number, number]] : []),
  ];
  const axis: ReactNode[] = [];
  for (let k = Math.ceil(a * 2); k <= Math.floor(b * 2); k++) {
    const t = k / 2;
    const whole = k % 2 === 0;
    axis.push(<rect key={`r${k}`} x={P(dx(t))} y={axY} width="1" height={whole ? 6 : 3} fill={`rgba(${CREAM},${whole ? 0.42 : 0.2})`} />);
    if (whole && (!narrow || t % 2 === 0))
      axis.push(
        <text key={`t${k}`} className={styles.mono} x={P(dx(t))} y={axY + 20} textAnchor="middle" fill="#aca491">
          {fmt(t, 0)}
        </text>,
      );
  }
  const first = cu.before[0];
  const lastAfter = cu.after[cu.after.length - 1];
  const quoteSize = narrow ? 15 : 17;

  return (
    <svg className={narrow ? styles.narrow : styles.wide} width="100%" height={height} aria-hidden="true" focusable="false">
      <defs>
        <pattern id={`${id}-hatch`} width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="1.2" height="5" fill={`rgba(${CREAM},.5)`} />
        </pattern>
        <pattern id={`${id}-held`} width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="1.6" height="5" fill="#e7a69d" />
        </pattern>
        <linearGradient id={`${id}-lit`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={`rgb(${LAMP})`} stopOpacity="0" />
          <stop offset=".55" stopColor={`rgb(${LAMP})`} stopOpacity=".07" />
          <stop offset="1" stopColor={`rgb(${LAMP})`} stopOpacity=".16" />
        </linearGradient>
      </defs>

      {/* the whole clip */}
      <text x="0" y={OY - 4} fill="#aca491" fontSize="12" fontWeight="500">
        The whole clip, {fmt(0, 0)} to {fmt(T, 0)}
      </text>
      {showcase.gaps.map((g) => (
        <g key={g.id}>
          <rect x={P(ox(g.start))} y={wy} width={P(ox(g.end) - ox(g.start))} height="18" fill={`rgba(${LAMP},.17)`} />
          <rect x={P(ox(g.start))} y={wy} width={P(ox(g.end) - ox(g.start))} height="1" fill={`rgba(${LAMP},.7)`} />
        </g>
      ))}
      {showcase.speech.map(([s, e]) => (
        <g key={s}>
          <rect x={P(ox(s))} y={sy} width={P(ox(e) - ox(s))} height="14" fill="#f6eedf" fillOpacity=".9" />
          {/* never thinner than 1.5px */}
          {ox(e) - ox(s) < 0.42 ? <rect x={P(ox(s))} y={sy} width="1.5" height="14" fill="#f6eedf" fillOpacity=".9" /> : null}
        </g>
      ))}
      {voiced.map((c) => (
        <rect key={c.id} x={P(ox(c.start))} y={wy + 5} width={P(ox(c.end) - ox(c.start))} height="8" fill="#e9cb98" />
      ))}
      <rect x="0" y={ay} width="100%" height="1" fill={`rgba(${CREAM},.14)`} />
      {ticks}

      {/* the lens, and the two lines down to the close-up */}
      <g stroke="#f6ddb0" strokeWidth="1.2" fill="none">
        <svg x={P(lx0)} overflow="visible">
          <path d={`M0 ${ly0 + br}V${ly0}H${br}M${br} ${ly1}H0V${ly1 - br}`} />
        </svg>
        <svg x={P(lx1)} overflow="visible">
          <path d={`M${-br} ${ly0}H0V${ly0 + br}M0 ${ly1 - br}V${ly1}H${-br}`} />
        </svg>
      </g>
      <line x1={P(lx0)} y1={ly1} x2="0" y2={D0} stroke={`rgba(${CREAM},.14)`} />
      <line x1={P(lx1)} y1={ly1} x2="100%" y2={D0} stroke={`rgba(${CREAM},.14)`} />

      {/* the close-up */}
      <rect x="0" y={top} width="100%" height={axY - top} fill="rgba(13,15,23,.5)" />
      <rect x="0" y={top} width="100%" height="1" fill={`rgba(${CREAM},.14)`} />
      <rect x={P(gx0)} y={top + 1} width={P(gx1 - gx0)} height={axY - top - 1} fill={`url(#${id}-lit)`} />
      {guards.map(([g0, g1]) => (
        <rect key={g0} x={P(dx(g0))} y={spY - 4} width={P(dx(g1) - dx(g0))} height={axY - spY + 4} fill={`url(#${id}-hatch)`} />
      ))}
      <rect x={P(gx0)} y={edgeY} width="1" height={axY - edgeY} fill="#e9cb98" />
      <rect x={P(gx1)} y={edgeY} width="1" height={axY - edgeY} fill="#e9cb98" transform="translate(-1 0)" />
      <text className={styles.mono} x={P(gx0)} dx="6" y={tcY} fill="#f6ddb0">
        {fmt(gap.start)}
      </text>
      <text className={styles.mono} x={P(gx1)} dx="-6" y={tcY} textAnchor="end" fill="#f6ddb0">
        {fmt(gap.end)}
      </text>
      {/* speech as heard, and the transcribed words inside it */}
      {cu.spans.map((m) => (
        <rect key={`s${m.start}`} x={P(dx(m.start))} y={spY} width={P(dx(m.end) - dx(m.start))} height={spH} fill="#f6eedf" fillOpacity={cu.words.length ? 0.34 : 0.92} />
      ))}
      {cu.words.map((m) => (
        <g key={`w${m.start}`}>
          <rect x={P(dx(m.start))} y={spY} width={P(dx(m.end) - dx(m.start))} height={spH} fill="#f6eedf" fillOpacity=".92" />
          {dx(m.end) - dx(m.start) < 0.6 ? <rect x={P(dx(m.start))} y={spY} width="2" height={spH} fill="#f6eedf" fillOpacity=".92" /> : null}
        </g>
      ))}
      {first ? (
        <text className={styles.word} x={P(dx(Math.max(first.start, a)))} y={spY - 9} fill="#d6cdbb" fontSize={quoteSize}>
          &ldquo;{cu.before.map((w) => w.text).join(" ")}&rdquo;
        </text>
      ) : null}
      {lastAfter ? (
        <text className={styles.word} x={P(dx(Math.min(lastAfter.end, b)))} y={spY - 9} textAnchor="end" fill="#d6cdbb" fontSize={quoteSize}>
          &ldquo;{cu.after.map((w) => w.text).join(" ")}&rdquo;
        </text>
      ) : null}
      {narrow ? null : (
        <>
          {cu.guardBefore ? (
            <text x={P(dx(gap.start - GUARD_S))} dx="-8" y={spY + spH + 16} textAnchor="end" fill="#aca491" fontSize="12">
              {GUARD_S.toFixed(2)} s guard
            </text>
          ) : null}
          {cu.guardAfter ? (
            <text x={P(dx(gap.end + GUARD_S))} dx="8" y={spY + spH + 16} fill="#aca491" fontSize="12">
              {GUARD_S.toFixed(2)} s guard
            </text>
          ) : null}
        </>
      )}
      <rect x={P(gx0)} y={wY} width={P(gx1 - gx0)} height={wH} fill={`rgba(${LAMP},.08)`} stroke={`rgba(${LAMP},.55)`} />
      {narrow ? (
        <>
          <text x={P(gx0)} dx="8" y={wY + 17} fill="#f6ddb0" fontSize="12.5" fontWeight="600">
            Usable window
          </text>
          <text x={P(gx0)} dx="8" y={wY + 32} fill="#d6cdbb" fontSize="12">
            {wLen} s, room for {gap.capacity} words
          </text>
        </>
      ) : (
        <text x={P(gx0)} dx="12" y={wY + 20} fill="#f6ddb0" fontSize="13" fontWeight="600">
          Usable window{" "}
          <tspan fill="#d6cdbb" fontWeight="400">
            {wLen} s, room for {gap.capacity} words
          </tspan>
        </text>
      )}

      {/* the placed line, and the long version that would not fit */}
      <rect x={P(cx0)} y={cy} width={P(cx1 - cx0)} height="22" fill="#e9cb98" />
      {narrow ? null : (
        <text className={styles.fit} x={P(cx0)} dx="10" y={cy + 15} fill="#13151f" fontSize="12.5" fontWeight="600">
          &ldquo;{cue.text}&rdquo;
        </text>
      )}
      <text x={P(cx1)} dx={narrow ? 7 : 10} y={cy + 15} fill="#f6ddb0" fontSize="12.5" fontWeight="600">
        {narrow ? "Placed" : "Version placed"}{" "}
        <tspan className={styles.mono} fill="#d6cdbb" fontWeight="400">
          {(cue.end - cue.start).toFixed(2)} s
        </tspan>
      </text>
      <rect x={P(cx0)} y={gy + 0.5} width={P(gx1 - cx0)} height="16" fill="none" stroke={`rgba(${CREAM},.5)`} strokeDasharray="4 3" transform="translate(.5 0)" />
      <rect x={P(gx1)} y={gy} width={P(dx(cu.longEnd) - gx1)} height="17" fill={`url(#${id}-held)`} />
      <rect x={P(gx1)} y={gy} width={P(dx(cu.longEnd) - gx1)} height="17" fill="none" stroke="#e7a69d" />
      {Number.isFinite(cu.nextSpeechStart) ? (
        <line
          x1={P(dx(cu.nextSpeechStart))}
          x2={P(dx(cu.nextSpeechStart))}
          y1={gy}
          y2={spY + spH}
          stroke="#e7a69d"
          strokeDasharray="2 3"
          transform="translate(.5 0)"
        />
      ) : null}
      <text x={P(cx0)} dx="10" y={gy + 12} fill="#aca491" fontSize="12">
        {narrow ? "Long version: too long" : "Long version: runs past the window and into the next word, so it isn’t used"}
      </text>

      {/* the close-up's own axis, and the verdict */}
      <rect x="0" y={axY} width="100%" height="1" fill={`rgba(${CREAM},.14)`} />
      {axis}
      <text x="100%" y={top - 12} textAnchor="end" fill={overlap > 0 ? "#e7a69d" : "#a7c8a1"} fontSize="12.5" fontWeight="600">
        Overlap with speech{" "}
        <tspan className={styles.mono} fill="#f6eedf" fontWeight="400">
          {overlap.toFixed(2)} s
        </tspan>
      </text>
      <text x="0" y={top - 12} fill="#aca491" fontSize="12" fontWeight="500">
        Close-up, {fmt(a, 1)} to {fmt(b, 1)}
      </text>
    </svg>
  );
}

const quoted = (words: { text: string }[]) => `‘${words.map((w) => w.text).join(" ")}’`;

/** The figure's text alternative, from the same numbers it draws. */
function figureLabel(s: Showcase, cu: CloseUp, placed: number): string {
  const { gap, cue, a, b } = cu;
  const n = s.gaps.length;
  const where = placed === n ? "each" : `${numberWord(placed)} of them`;
  const before = cu.before.length ? `the ${cu.before.length === 1 ? "word" : "words"} ${quoted(cu.before)}` : "speech";
  const after = cu.after.length ? `the ${cu.after.length === 1 ? "word" : "words"} ${quoted(cu.after)}` : "the next speech";
  const afterShort = cu.after.length ? quoted(cu.after) : "the next speech";
  const guard = `a ${GUARD_S.toFixed(2)} second guard`;
  const opening = Number.isFinite(cu.prevSpeechEnd)
    ? `${before} ${cu.before.length === 1 ? "ends" : "end"} at ${fmt(cu.prevSpeechEnd)}, ${cu.guardBefore ? `${guard} follows, ` : ""}`
    : "";
  const closing = Number.isFinite(cu.nextSpeechStart)
    ? `, then ${cu.guardAfter ? `${cu.guardBefore ? "another" : "a"} guard before ` : ""}${after} at ${fmt(cu.nextSpeechStart)}`
    : "";
  return (
    `Timeline of the ${Math.round(s.duration)}-second clip: speech blocks, ${numberWord(n)} usable windows and a description placed inside ${where}. ` +
    `Below, a close-up of ${fmt(a, 1)} to ${fmt(b, 1)}: ${opening}` +
    `the usable window runs from ${fmt(gap.start)} to ${fmt(gap.end)} with room for ${gap.capacity} words${closing}. ` +
    `The short version of the line, ‘${cue.text}’, sits inside the window. The long version would run past the window into ${afterShort}, so it is not used.`
  );
}

/** 4. The guarantee: the model writes, a solver places, and the figure shows the fit. */
export function Guarantee({ showcase }: { showcase: Showcase }) {
  const cu = closeUp(showcase);
  const voiced = voicedCues(showcase);
  const overlap = speechOverlap(showcase).seconds;
  const placed = new Set(voiced.map((c) => c.gap.id)).size;
  // the placed line carries its text once the figure is wide enough for it
  const fitAt = cu ? Math.ceil((textPx(cu.cue.text) * (cu.b - cu.a)) / Math.max(1e-3, cu.cue.end - cu.cue.start)) : 0;

  return (
    <Section id="guarantee" eyebrow="The guarantee" heading="Zero overlap, by construction." headCols={12} className={styles.guarantee}>
      <div className={styles.intro}>
        <div className={styles.body}>
          <p>
            Language models are bad at timing. On the timing-aware scoring in <cite>REFRAMED</cite> (COLM 2026), the best model
            reached 17.3%; human describers reached 61.2%. So Lull splits the job. The model writes. A constraint solver places
            each line in a window measured from the audio, and a run that overlaps a single word can&rsquo;t publish.
          </p>
        </div>
        <ul className={styles.rules}>
          <li>
            <span className={styles.k}>Guard</span>
            <span className={styles.v}>
              <span className="t-mono">{GUARD_S.toFixed(2)} s</span> of clear air on each side of every word.
            </span>
          </li>
          <li>
            <span className={styles.k}>Durations</span>
            <span className={styles.v}>Measured from the voiced audio, never estimated by a model.</span>
          </li>
          <li>
            <span className={styles.k}>Too long</span>
            <span className={styles.v}>
              Sped up to at most <span className="t-mono">1.10×</span>, or replaced by a shorter version.
            </span>
          </li>
          <li>
            <span className={styles.k}>Publish gate</span>
            <span className={styles.v}>
              Overlap with speech must be <span className="t-mono">0.00 s</span>.
            </span>
          </li>
        </ul>
      </div>
      {cu ? (
        <figure className={styles.figure}>
          <style href={`guarantee-fit-${fitAt}`} precedence="default">{`@container gfig (min-width:${fitAt}px){.${styles.host} .${styles.fit}{display:inline}}`}</style>
          <div className={styles.host} role="img" aria-label={figureLabel(showcase, cu, placed)}>
            <Figure narrow={false} showcase={showcase} cu={cu} voiced={voiced} overlap={overlap} />
            <Figure narrow showcase={showcase} cu={cu} voiced={voiced} overlap={overlap} />
          </div>
          <figcaption className={styles.foot}>
            <ul className={styles.legend} aria-hidden="true">
              <li>
                <i className={cx(styles.sw, styles.swSpeech)} />
                Speech
              </li>
              <li>
                <i className={cx(styles.sw, styles.swGuard)} />
                Guard, {GUARD_S.toFixed(2)} s
              </li>
              <li>
                <i className={cx(styles.sw, styles.swWindow)} />
                Usable window
              </li>
              <li>
                <i className={cx(styles.sw, styles.swCue)} />
                Description audio
              </li>
              <li>
                <i className={cx(styles.sw, styles.swGhost)} />
                Version not used
              </li>
            </ul>
            <p className={cx("t-small", styles.note)}>
              Speech, words, guards, windows and the line&rsquo;s voiced length come from the real run; the long version is drawn for
              illustration.
            </p>
          </figcaption>
        </figure>
      ) : null}
    </Section>
  );
}
