/**
 * Draws the horizon: the soundtrack lying on the floor as one line. Speech moves the line
 * with its real loudness; the pauses lie flat, each lit from above; the voice draws itself
 * in warm light inside its pause as it is spoken.
 */
const LAMP = "233,203,152";
const CREAM = "246,238,223";

export type HorizonModel = {
  duration: number;
  gaps: readonly { id: string; start: number; end: number }[];
  /** Voiced cues only, with the id of their pause. */
  cues: readonly { start: number; end: number; gapId: string }[];
  /** Line amplitude per 100 ms frame, 0..1; 0 outside speech. */
  amp: readonly number[];
};

export type HorizonFrame = {
  /** CSS pixels. */
  width: number;
  height: number;
  dpr: number;
  /** Inset of the time axis from each edge, and the y of the line. */
  pad: number;
  lineY: number;
  /** Entrance sweep, 0..1. */
  enter: number;
  t: number;
  started: boolean;
  ended: boolean;
  /** How much of each cue has been spoken, 0..1. */
  progress: readonly number[];
};

/** dBFS to the line's amplitude: -58 dB and below is flat, -26 dB is full. */
export const amplitude = (db: number) => Math.pow(Math.min(1, Math.max(0, (db + 58) / 32)), 1.25);

const easeOut = (x: number) => 1 - Math.pow(1 - x, 4);

export function drawHorizon(ctx: CanvasRenderingContext2D, m: HorizonModel, f: HorizonFrame) {
  const { width: W, height: H, pad: PAD, lineY: LINE, dpr } = f;
  if (!W) return;
  const T = m.duration;
  const X = (t: number) => PAD + (t / T) * (W - 2 * PAD);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  const e = easeOut(f.enter);
  const AMAX = Math.min(38, LINE * 0.23);

  // 1. lamp pools: a soft column of light over each pause, brighter while a line is spoken there
  for (const g of m.gaps) {
    const x0 = X(g.start);
    const x1 = X(g.end);
    const w = x1 - x0;
    const cx = (x0 + x1) / 2;
    const speaking = f.started && m.cues.some((c) => c.gapId === g.id && f.t >= c.start && f.t <= c.end) ? 1 : 0;
    const lit = (0.85 + 0.6 * speaking) * e;
    ctx.save();
    if ("filter" in ctx) ctx.filter = "blur(10px)";
    const grad = ctx.createLinearGradient(0, 0, 0, LINE);
    grad.addColorStop(0, `rgba(${LAMP},0)`);
    grad.addColorStop(0.45, `rgba(${LAMP},${0.05 * lit})`);
    grad.addColorStop(1, `rgba(${LAMP},${0.2 * lit})`);
    ctx.fillStyle = grad;
    const topW = w * 0.62;
    ctx.beginPath();
    ctx.moveTo(cx - topW / 2, 4);
    ctx.lineTo(cx + topW / 2, 4);
    ctx.lineTo(x1 + 4, LINE);
    ctx.lineTo(x0 - 4, LINE);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
    // floor glow where the light lands
    ctx.save();
    ctx.translate(cx, LINE);
    ctx.scale(w / 2 + 14, 13);
    const rg = ctx.createRadialGradient(0, 0, 0, 0, 0, 1);
    rg.addColorStop(0, `rgba(${LAMP},${0.42 * lit})`);
    rg.addColorStop(0.6, `rgba(${LAMP},${0.12 * lit})`);
    rg.addColorStop(1, `rgba(${LAMP},0)`);
    ctx.fillStyle = rg;
    ctx.beginPath();
    ctx.arc(0, 0, 1, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
    // the measured edges of the window
    ctx.fillStyle = `rgba(${LAMP},${0.75 * e})`;
    ctx.fillRect(Math.round(x0), LINE - 8, 1, 16);
    ctx.fillRect(Math.round(x1) - 1, LINE - 8, 1, 16);
  }

  // 2. the line, clipped by the entrance sweep
  ctx.save();
  ctx.beginPath();
  ctx.rect(0, 0, W * e + 1, H);
  ctx.clip();
  // the horizon runs on past the clip, faintly, to both edges
  ctx.fillStyle = `rgba(${CREAM},.16)`;
  ctx.fillRect(0, LINE, PAD, 1);
  ctx.fillRect(W - PAD, LINE, PAD, 1);
  ctx.fillStyle = `rgba(${CREAM},.42)`;
  ctx.fillRect(PAD, LINE, W - 2 * PAD, 1);
  // speech, with its real amplitude
  const path = new Path2D();
  const refl = new Path2D();
  let open = false;
  let openR = false;
  for (let i = 0; i < m.amp.length; i++) {
    const a = m.amp[i] ?? 0;
    const x = X(i / 10);
    if (a > 0) {
      const y = LINE + 0.5 + (i % 2 ? -1 : 1) * Math.max(1.2, a * AMAX);
      if (!open) {
        path.moveTo(x, LINE + 0.5);
        open = true;
      }
      path.lineTo(x, y);
      const yr = LINE + 7 + Math.max(0.6, a * AMAX * 0.42) * (i % 2 ? 1 : 0.2);
      if (!openR) {
        refl.moveTo(x, LINE + 7);
        openR = true;
      }
      refl.lineTo(x, yr);
    } else {
      if (open) {
        path.lineTo(x, LINE + 0.5);
        open = false;
      }
      if (openR) {
        refl.lineTo(x, LINE + 7);
        openR = false;
      }
    }
  }
  ctx.lineJoin = "round";
  ctx.shadowColor = `rgba(${CREAM},.55)`;
  ctx.shadowBlur = 8;
  ctx.strokeStyle = `rgba(${CREAM},.95)`;
  ctx.lineWidth = 1.1;
  ctx.stroke(path);
  ctx.shadowBlur = 0;
  // its reflection on the floor
  const rgr = ctx.createLinearGradient(0, LINE + 6, 0, LINE + 6 + AMAX * 0.5);
  rgr.addColorStop(0, `rgba(${CREAM},.22)`);
  rgr.addColorStop(1, `rgba(${CREAM},0)`);
  ctx.strokeStyle = rgr;
  ctx.lineWidth = 1;
  ctx.stroke(refl);
  ctx.restore();

  // 3. the voice: a warm line drawing itself inside each pause
  m.cues.forEach((c, i) => {
    const p = f.progress[i] ?? 0;
    if (p <= 0) return;
    const x0 = X(c.start);
    const x1 = X(c.start + (c.end - c.start) * p);
    ctx.save();
    ctx.shadowColor = `rgba(${LAMP},.85)`;
    ctx.shadowBlur = 14;
    ctx.fillStyle = `rgb(${LAMP})`;
    ctx.fillRect(x0, LINE - 1.5, Math.max(1, x1 - x0), 3);
    ctx.restore();
    ctx.fillStyle = `rgba(${LAMP},.16)`;
    ctx.fillRect(x0, LINE + 7, Math.max(1, x1 - x0), 2);
    if (p < 1) {
      ctx.fillStyle = "#fff7e6";
      ctx.fillRect(x1 - 1.5, LINE - 3, 3, 6);
    }
  });

  // 4. the ruler: a tick a second, taller every 5 and 10
  const yR = H - 26;
  for (let s = 0; s <= T; s++) {
    const x = Math.round(X(s));
    const big = s % 10 === 0;
    const mid = s % 5 === 0;
    const h = big ? 8 : mid ? 5 : 3;
    ctx.fillStyle = `rgba(${CREAM},${big ? 0.42 : mid ? 0.24 : 0.12})`;
    ctx.fillRect(x, yR - h, 1, h);
  }

  // 5. the playhead
  if (f.started && !f.ended) {
    const x = Math.round(X(f.t)) + 0.5;
    ctx.fillStyle = `rgba(${CREAM},.9)`;
    const yTop = LINE - 20;
    ctx.fillRect(x - 0.5, yTop, 1, yR - yTop);
    ctx.fillRect(x - 3, yTop - 3, 7, 7);
  }
}
