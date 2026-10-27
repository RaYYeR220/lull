#!/usr/bin/env node
/**
 * Builds the embeddable player: player/src/lull-player.ts -> public/player.js
 * (one ES module, minified). Fails when the gzipped file passes the 30 kB budget.
 *
 *   node scripts/build-player.mjs
 */
import { readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";
import { build } from "esbuild";

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const OUT = path.join(ROOT, "public", "player.js");
const BUDGET_GZ = 30 * 1024;

await build({
  entryPoints: [path.join(ROOT, "player", "src", "lull-player.ts")],
  outfile: OUT,
  bundle: true,
  format: "esm",
  minify: true,
  target: ["es2020", "chrome100", "firefox100", "safari15"],
  legalComments: "none",
  logLevel: "warning",
});

const raw = statSync(OUT).size;
const gz = gzipSync(readFileSync(OUT), { level: 9 }).length;
const kb = (n) => `${(n / 1024).toFixed(1)} kB`;
console.log(`player: public/player.js ${kb(raw)} (${kb(gz)} gzipped, budget ${kb(BUDGET_GZ)})`);
if (gz > BUDGET_GZ) {
  console.error(`player: ${kb(gz)} gzipped is over the ${kb(BUDGET_GZ)} budget`);
  process.exit(1);
}
