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
 */
import { copyFileSync, existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, realpathSync, renameSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { LAYOUT, SHOWCASE_ID, indexEntry, parseIndex, parseShowcase, requiredFiles } from "../lib/showcase-schema.mjs";

/** public/showcase/ of this site. */
export const PUBLIC_SHOWCASE = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "public", "showcase");
const INDEX = "index.json";

export class SyncError extends Error {
  constructor(message) {
    super(message);
    this.name = "SyncError";
  }
}

/** True when `inner` is `outer` itself or lies under it. */
const under = (outer, inner) => {
  const back = path.relative(outer, inner);
  return !back.startsWith("..") && !path.isAbsolute(back);
};

/**
 * dir/rel when it is a regular file inside dir; null when there is nothing there. A link is
 * refused, and so is a file whose real path leaves dir: a linked folder (a symlink or a
 * Windows junction) anywhere on the way to it.
 */
function fileIn(dir, rel) {
  const full = path.join(dir, ...rel.split("/"));
  const back = path.relative(dir, full);
  if (!back || back.startsWith("..") || path.isAbsolute(back)) throw new SyncError(`${rel} is not inside ${dir}`);
  let st;
  try {
    st = lstatSync(full);
  } catch {
    return null;
  }
  if (st.isSymbolicLink()) throw new SyncError(`${rel} is a link; put the file itself in the export`);
  if (!st.isFile()) return null;
  if (!under(realpathSync(dir), realpathSync(full)))
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
 * Returns the relative paths copied and the new index.
 */
export function syncShowcase({ src, id, dest = PUBLIC_SHOWCASE }) {
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
  try {
    for (const rel of files) {
      const to = path.join(staging, ...rel.split("/"));
      mkdirSync(path.dirname(to), { recursive: true });
      copyFileSync(fileIn(from, rel), to);
    }
    rmSync(target, { recursive: true, force: true });
    renameSync(staging, target);
  } finally {
    rmSync(staging, { recursive: true, force: true });
  }
  return { files, index: writeIndex(dest) };
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
    const { files, index } = syncShowcase({ src, id });
    console.log(`showcase ${id}: copied ${files.length} files to public/showcase/${id}/; the index lists ${index.map((e) => e.id).join(", ")}`);
  } catch (e) {
    console.error(`sync-showcase: ${e instanceof Error ? e.message : String(e)}`);
    process.exit(1);
  }
}
