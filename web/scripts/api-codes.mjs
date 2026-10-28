#!/usr/bin/env node
/**
 * Every refusal code the API raises, with its statuses, read from the API's source: each
 * `LiveError(<status>, "<code>", …)` in engine/src/lull/api/*.py.
 *
 *   node scripts/api-codes.mjs [apiSrcDir]    writes test/fixtures/api-codes.json
 *
 * The folder is the argument, else LULL_API_SRC, else a lull-api checkout beside this one.
 * The snapshot is what the unit tests check the reference against, so they run anywhere; where
 * the source is present, a test also checks the snapshot still matches it.
 */
import { existsSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const WEB = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
export const SNAPSHOT = path.join(WEB, "test", "fixtures", "api-codes.json");

/** Where the API's source is expected. */
export function apiSrc(arg = process.env.LULL_API_SRC) {
  return path.resolve(arg || path.join(WEB, "..", "..", "lull-api", "engine", "src", "lull", "api"));
}

/** {code: [statuses]} from the .py files in `dir`, sorted. */
export function readCodes(dir) {
  const raised = {};
  for (const f of readdirSync(dir).filter((x) => x.endsWith(".py")).sort()) {
    for (const m of readFileSync(path.join(dir, f), "utf8").matchAll(/LiveError\(\s*(\d{3}),\s*"([a-z_]+)"/g)) {
      const st = Number(m[1]);
      raised[m[2]] = [...new Set([...(raised[m[2]] ?? []), st])].sort((a, b) => a - b);
    }
  }
  return Object.fromEntries(Object.entries(raised).sort(([a], [b]) => a.localeCompare(b)));
}

const run = process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href;
if (run) {
  const dir = apiSrc(process.argv[2]);
  if (!existsSync(dir)) {
    console.error(`no API source at ${dir}; pass its folder or set LULL_API_SRC`);
    process.exit(2);
  }
  const codes = readCodes(dir);
  writeFileSync(SNAPSHOT, `${JSON.stringify({ from: "lull-api engine/src/lull/api/*.py", codes }, null, 2)}\n`);
  console.log(`${Object.keys(codes).length} codes written to ${path.relative(WEB, SNAPSHOT)}`);
}
