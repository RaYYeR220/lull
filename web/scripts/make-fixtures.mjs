#!/usr/bin/env node
/**
 * Generates the small media the player tests run against, in e2e/fixtures/ (gitignored).
 *
 *   node scripts/make-fixtures.mjs [--force]
 *
 * A 6 s test-pattern video with a quiet tone for the programme, the description tracks in
 * the engine's formats (descriptions.vtt, extended.vtt with its NOTE pause line), captions,
 * a mixed described-audio.m4a and one WAV per cue, named by cue id as the engine names
 * them. Needs ffmpeg on PATH. Skips the work when the files for this version are there.
 */
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const VERSION = "4";
const OUT = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "e2e", "fixtures");
const STAMP = path.join(OUT, ".version");

/** Cue timings the e2e tests assert against; keep in step with e2e/player-fixtures.ts. */
const CUES = [
  { id: "c001", start: 1.0, dur: 1.2, text: "A grey card slides in from the left." },
  { id: "c002", start: 3.6, dur: 1.0, text: "The card turns over & shows a clock." },
];
const EXTENDED = [{ id: "x001", start: 2.5, dur: 1.5, text: "Two words appear on the card: still here." }];
/**
 * Recordings as the engine's export names them (`audio/<cue id>.m4a`), except c002, which only
 * has a .wav: the player must try .wav when the .m4a isn't there.
 */
const CLIP_EXT = { c001: "m4a", c002: "wav", x001: "m4a" };
const DURATION = 6;

const FILES = [
  "video.mp4",
  "described-audio.m4a",
  "descriptions.vtt",
  "extended.vtt",
  "captions.vtt",
  "poster.jpg",
  "extended-long.vtt",
  ...[...CUES, ...EXTENDED].map((c) => `audio/${c.id}.${CLIP_EXT[c.id]}`),
];

const force = process.argv.includes("--force");
const fresh = !force && existsSync(STAMP) && readFileSync(STAMP, "utf8").trim() === VERSION && FILES.every((f) => existsSync(path.join(OUT, f)));
if (fresh) {
  console.log(`fixtures: up to date in ${path.relative(process.cwd(), OUT) || "."}`);
  process.exit(0);
}

function ffmpeg(args) {
  try {
    execFileSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", ...args], { stdio: ["ignore", "inherit", "inherit"] });
  } catch (err) {
    if (err && err.code === "ENOENT") {
      console.error("fixtures: ffmpeg is not on PATH; install it to generate the player test media");
      process.exit(1);
    }
    throw err;
  }
}

const tc = (t) => {
  const ms = Math.round(t * 1000);
  const h = String(Math.floor(ms / 3_600_000)).padStart(2, "0");
  const m = String(Math.floor(ms / 60_000) % 60).padStart(2, "0");
  const s = String(Math.floor(ms / 1000) % 60).padStart(2, "0");
  return `${h}:${m}:${s}.${String(ms % 1000).padStart(3, "0")}`;
};
const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const header = "WEBVTT\nKind: descriptions\nLanguage: en\n";

mkdirSync(path.join(OUT, "audio"), { recursive: true });

// the programme: test pattern + a quiet 220 Hz tone
ffmpeg([
  "-f", "lavfi", "-i", `testsrc2=size=320x180:rate=24:duration=${DURATION}`,
  "-f", "lavfi", "-i", `sine=frequency=220:sample_rate=48000:duration=${DURATION}`,
  "-filter:a", "volume=0.25",
  "-c:v", "libx264", "-profile:v", "baseline", "-pix_fmt", "yuv420p", "-g", "12",
  "-c:a", "aac", "-b:a", "64k", "-ac", "1",
  "-shortest", "-movflags", "+faststart",
  path.join(OUT, "video.mp4"),
]);
ffmpeg(["-ss", "0.5", "-i", path.join(OUT, "video.mp4"), "-frames:v", "1", "-q:v", "4", path.join(OUT, "poster.jpg")]);

// one clip per cue: a 660 Hz tone as long as the line
for (const c of [...CUES, ...EXTENDED]) {
  const ext = CLIP_EXT[c.id];
  const codec = ext === "m4a" ? ["-c:a", "aac", "-b:a", "64k"] : [];
  ffmpeg(["-f", "lavfi", "-i", `sine=frequency=660:sample_rate=24000:duration=${c.dur}`, "-filter:a", "volume=0.4", "-ac", "1", ...codec, path.join(OUT, "audio", `${c.id}.${ext}`)]);
}

// the mixed track: the programme tone ducked under each cue's tone
const duck = CUES.map((c) => `between(t,${c.start},${c.start + c.dur})`).join("+");
ffmpeg([
  "-f", "lavfi", "-i", `sine=frequency=220:sample_rate=48000:duration=${DURATION}`,
  "-f", "lavfi", "-i", `sine=frequency=660:sample_rate=48000:duration=${DURATION}`,
  "-filter_complex",
  `[0:a]volume='if(${duck},0.08,0.25)':eval=frame[p];[1:a]volume='if(${duck},0.4,0)':eval=frame[d];[p][d]amix=inputs=2:normalize=0[m]`,
  "-map", "[m]", "-c:a", "aac", "-b:a", "64k", "-ac", "1",
  path.join(OUT, "described-audio.m4a"),
]);

writeFileSync(
  path.join(OUT, "descriptions.vtt"),
  [header, ...CUES.map((c) => `${c.id}\n${tc(c.start)} --> ${tc(c.start + c.dur)}\n${esc(c.text)}\n`)].join("\n"),
);
writeFileSync(
  path.join(OUT, "extended.vtt"),
  [header, ...EXTENDED.map((c) => `NOTE extended pause=${c.dur.toFixed(3)}\n\n${c.id}\n${tc(c.start)} --> ${tc(c.start + c.dur)}\n${esc(c.text)}\n`)].join("\n"),
);
// pauses far past what a viewer should sit through: one by NOTE, one by the cue's span
writeFileSync(
  path.join(OUT, "extended-long.vtt"),
  `${header}
NOTE extended pause=999

x101
00:00:01.000 --> 00:00:02.000
A wait that would stall the video.

x102
00:00:04.000 --> 00:01:44.000
A line with no pause note and a long span.
`,
);
writeFileSync(path.join(OUT, "captions.vtt"), "WEBVTT\n\n00:00:00.200 --> 00:00:02.800\n[a low steady tone]\n");
writeFileSync(STAMP, `${VERSION}\n`);
console.log(`fixtures: wrote ${FILES.length} files to ${path.relative(process.cwd(), OUT) || "."}`);
