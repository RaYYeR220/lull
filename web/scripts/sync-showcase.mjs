#!/usr/bin/env node
/**
 * Copies one engine showcase export (`lull export-showcase RUN_DIR OUT_DIR`) into the site
 * and lists it in public/showcase/index.json.
 *
 *   node scripts/sync-showcase.mjs <srcDir> <id>
 *
 * showcase.json is checked against the schema first (lib/showcase-schema.mjs): an interim
 * placeholder, a path that is absolute or leaves the folder, an id that doesn't match, or a
 * file the player needs that isn't there each stop the sync with the reason, and the site's
 * copy is left as it was. Only the files the site plays are copied (showcase.json as it is,
 * video.mp4, described-audio.m4a, the two VTTs, audio/<cue>.m4a, keyframes/<cue>.jpg), into
 * a fresh folder that then replaces public/showcase/<id>/. The index keeps its order and adds
 * a new showcase at the end; every row is rebuilt from its showcase.json.
 *
 * The site's media is kept small on the way in (ffmpeg; the timing never moves):
 * - a video over 15 MiB is re-encoded at most 480 lines high, H.264 CRF 30, its sound copied
 *   as it is; if that is still over 25 MiB, it is made from the export again at 360 lines,
 *   CRF 30, its sound as AAC at 64 kbit/s;
 * - a described mix over 8 MiB is re-encoded as AAC at 64 kbit/s;
 * - a poster, poster.jpg, is grabbed from the video at 1 s, at most 60 KiB, for the player to
 *   show before it plays (a poster that can't be made is reported, and the sync goes on).
 */
import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, realpathSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { LAYOUT, SHOWCASE_ID, indexEntry, parseIndex, parseShowcase, requiredFiles } from "../lib/showcase-schema.mjs";

/** public/showcase/ of this site. */
export const PUBLIC_SHOWCASE = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "public", "showcase");
const INDEX = "index.json";
/** A video larger than this is re-encoded on the way in, at most 480 lines high. */
export const MAX_VIDEO_BYTES = 15 * 1024 * 1024;
/** A 480-line video still larger than this is made again at 360 lines. */
export const MAX_480_BYTES = 25 * 1024 * 1024;
/** A described mix larger than this is re-encoded at 64 kbit/s. */
export const MAX_DESCRIBED_BYTES = 8 * 1024 * 1024;
/** The poster's largest size. */
export const MAX_POSTER_BYTES = 60 * 1024;

/**
 * ffmpeg arguments that re-encode `from` to `to`: at most `lines` high, H.264 CRF 30; the sound
 * copied as it is, or ("aac64") as AAC at 64 kbit/s.
 */
export function reencodeArgs(from, to, lines = 480, sound = "copy") {
  return [
    "-v", "error", "-y", "-i", from,
    "-vf", `scale=-2:'min(${lines},ih)'`,
    "-c:v", "libx264", "-preset", "slow", "-crf", "30", "-pix_fmt", "yuv420p",
    ...(sound === "copy" ? ["-c:a", "copy"] : ["-c:a", "aac", "-b:a", "64k"]),
    "-movflags", "+faststart",
    to,
  ];
}

/** ffmpeg arguments that re-encode an audio file as AAC at 64 kbit/s. */
export const audioArgs = (from, to) => ["-v", "error", "-y", "-i", from, "-vn", "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", to];

/** ffmpeg arguments that grab the frame at 1 s as a JPEG of quality `q` (2 best, 31 worst). */
export const posterArgs = (from, to, q) => ["-v", "error", "-y", "-ss", "1", "-i", from, "-frames:v", "1", "-q:v", String(q), to];

function ffmpegRun(args, what) {
  const r = spawnSync("ffmpeg", args, { stdio: ["ignore", "ignore", "pipe"], encoding: "utf8" });
  if (r.error || r.status !== 0) throw new SyncError(`${what} failed: ${r.error?.message ?? r.stderr?.trim() ?? `exit ${r.status}`}`);
}

/** The media steps, through ffmpeg; the tests pass their own. */
export const FFMPEG = {
  video: (from, to, lines, sound) => ffmpegRun(reencodeArgs(from, to, lines, sound), `re-encoding ${LAYOUT.video}`),
  audio: (from, to) => ffmpegRun(audioArgs(from, to), "re-encoding the described mix"),
  poster: (from, to) => {
    // the best quality that fits
    for (let q = 3; q <= 15; q += 2) {
      ffmpegRun(posterArgs(from, to, q), "the poster");
      if (statSync(to).size <= MAX_POSTER_BYTES) return;
    }
    throw new SyncError(`the poster is over ${MAX_POSTER_BYTES} bytes even at the lowest quality tried`);
  },
};

export class SyncError extends Error {
  constructor(message) {
    super(message);
    this.name = "SyncError";
  }
}

/**
 * True when `inner` is `outer` itself or lies under it, judged by whole path segments: a
 * folder whose name merely starts with two dots ("..cache") is inside, "../x" is not, and
 * another drive (an absolute relative path) is not.
 */
export function isInside(outer, inner) {
  const back = path.relative(outer, inner);
  if (path.isAbsolute(back)) return false;
  const first = back.split(path.sep)[0];
  return first !== "..";
}

/**
 * dir/rel when it is a regular file inside dir; null when there is nothing there. A link is
 * refused, and so is a file whose real path leaves dir: a linked folder (a symlink or a
 * Windows junction) anywhere on the way to it.
 */
function fileIn(dir, rel) {
  const full = path.join(dir, ...rel.split("/"));
  if (!isInside(dir, full) || path.relative(dir, full) === "") throw new SyncError(`${rel} is not inside ${dir}`);
  let st;
  try {
    st = lstatSync(full);
  } catch {
    return null;
  }
  if (st.isSymbolicLink()) throw new SyncError(`${rel} is a link; put the file itself in the export`);
  if (!st.isFile()) return null;
  if (!isInside(realpathSync(dir), realpathSync(full)))
    throw new SyncError(`${rel} resolves outside ${dir} through a linked folder; put the files themselves in the export`);
  return full;
}

function readJson(file, what) {
  try {
    return JSON.parse(readFileSync(file, "utf8"));
  } catch (e) {
    throw new SyncError(`${what} is not readable JSON: ${e instanceof Error ? e.message : String(e)}`);
  }
}

/**
 * Validates the export in `src` and copies it to `<dest>/<id>/`, then rewrites the index.
 * Returns the relative paths copied, the new index, and the video's size before and after
 * (`reencoded` when it was over `maxVideoBytes`).
 */
export function syncShowcase({ src, id, dest = PUBLIC_SHOWCASE, maxVideoBytes = MAX_VIDEO_BYTES, max480Bytes = MAX_480_BYTES, maxDescribedBytes = MAX_DESCRIBED_BYTES, tools = FFMPEG }) {
  if (typeof id !== "string" || !SHOWCASE_ID.test(id)) throw new SyncError(`the id must be lowercase letters, digits and dashes, not ${JSON.stringify(id)}`);
  const from = path.resolve(src);
  if (!existsSync(from) || !lstatSync(from).isDirectory()) throw new SyncError(`${from} is not a folder`);
  const json = fileIn(from, LAYOUT.json);
  if (!json) throw new SyncError(`${from} has no ${LAYOUT.json}: not a showcase export`);

  const raw = readJson(json, LAYOUT.json);
  if (raw && typeof raw === "object" && raw.interim === true) throw new SyncError(`${LAYOUT.json} is an interim placeholder, not an engine export`);
  const show = parseShowcase(raw, id);

  const needed = requiredFiles(raw);
  const missing = needed.filter((rel) => !fileIn(from, rel));
  if (missing.length) throw new SyncError(`the export is missing ${missing.join(", ")}`);
  const frames = show.cues.map((c) => `${LAYOUT.keyframes}${c.id}.jpg`).filter((rel) => fileIn(from, rel));
  const files = [...new Set([...needed, ...frames])].sort();

  mkdirSync(dest, { recursive: true });
  const target = path.join(dest, id);
  const staging = path.join(dest, `.${id}.sync-${process.pid}`);
  rmSync(staging, { recursive: true, force: true });
  let video;
  let described = null;
  let poster;
  try {
    for (const rel of files) {
      const to = path.join(staging, ...rel.split("/"));
      mkdirSync(path.dirname(to), { recursive: true });
      copyFileSync(fileIn(from, rel), to);
    }
    const staged = path.join(staging, LAYOUT.video);
    video = { bytes: statSync(staged).size, reencoded: false };
    if (video.bytes > maxVideoBytes) {
      const smaller = path.join(staging, `.small-${LAYOUT.video}`);
      let lines = 480;
      tools.video(staged, smaller, 480, "copy");
      if (statSync(smaller).size > max480Bytes) {
        lines = 360;
        tools.video(staged, smaller, 360, "aac64");
      }
      renameSync(smaller, staged);
      video = { bytes: statSync(staged).size, reencoded: true, from: video.bytes, lines };
    }
    const mix = typeof raw.audio?.described === "string" ? path.join(staging, ...raw.audio.described.split("/")) : null;
    if (mix && existsSync(mix) && statSync(mix).size > maxDescribedBytes) {
      const before = statSync(mix).size;
      const smaller = path.join(path.dirname(mix), `.small-${path.basename(mix)}`);
      tools.audio(mix, smaller);
      renameSync(smaller, mix);
      described = { bytes: statSync(mix).size, from: before };
    }
    const still = path.join(staging, LAYOUT.poster);
    try {
      tools.poster(staged, still);
      poster = { bytes: statSync(still).size };
    } catch (e) {
      rmSync(still, { force: true });
      poster = { error: e instanceof Error ? e.message : String(e) };
    }
    rmSync(target, { recursive: true, force: true });
    renameSync(staging, target);
  } finally {
    rmSync(staging, { recursive: true, force: true });
  }
  return { files, index: writeIndex(dest), video, described, poster };
}

/**
 * Rewrites `<dest>/index.json` from the showcase folders there: the existing order first,
 * new showcases after it in id order, folders that are gone dropped.
 */
export function writeIndex(dest = PUBLIC_SHOWCASE) {
  const file = path.join(dest, INDEX);
  let order = [];
  if (existsSync(file)) {
    try {
      order = parseIndex(JSON.parse(readFileSync(file, "utf8"))).map((e) => e.id);
    } catch {
      order = []; // a broken index is rebuilt from the folders
    }
  }
  const present = readdirSync(dest, { withFileTypes: true })
    .filter((d) => d.isDirectory() && SHOWCASE_ID.test(d.name) && existsSync(path.join(dest, d.name, LAYOUT.json)))
    .map((d) => d.name)
    .sort();
  const ids = [...order.filter((i) => present.includes(i)), ...present.filter((i) => !order.includes(i))];
  const entries = ids.map((i) => indexEntry(parseShowcase(readJson(path.join(dest, i, LAYOUT.json), `${i}/${LAYOUT.json}`), i)));
  writeFileSync(file, `${JSON.stringify(entries, null, 2)}\n`);
  return entries;
}

const run = process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href;
if (run) {
  const [src, id] = process.argv.slice(2);
  if (!src || !id) {
    console.error("usage: node scripts/sync-showcase.mjs <srcDir> <id>");
    process.exit(2);
  }
  try {
    const { files, index, video, described, poster } = syncShowcase({ src, id });
    const mb = (n) => `${(n / 1024 / 1024).toFixed(1)} MiB`;
    const size = video.reencoded ? `re-encoded at ${video.lines} lines, ${mb(video.from)} to ${mb(video.bytes)}` : mb(video.bytes);
    const mix = described ? `; described mix ${mb(described.from)} to ${mb(described.bytes)}` : "";
    const still = poster.error ? `; no poster: ${poster.error}` : `; poster ${Math.round(poster.bytes / 1024)} KiB`;
    console.log(`showcase ${id}: copied ${files.length} files to public/showcase/${id}/ (video ${size}${mix}${still}); the index lists ${index.map((e) => e.id).join(", ")}`);
  } catch (e) {
    console.error(`sync-showcase: ${e instanceof Error ? e.message : String(e)}`);
    process.exit(1);
  }
}
