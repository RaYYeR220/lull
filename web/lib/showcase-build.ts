import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";

/**
 * Stops a production build that would ship a placeholder: any <root>/<id>/showcase.json
 * marked `"interim": true` (the design-data stand-in used before the engine's export).
 * The site's numbers must come from a real run.
 */
export function assertRealShowcases(root: string): void {
  if (!existsSync(root)) return;
  const interim = readdirSync(root, { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => path.join(root, d.name, "showcase.json"))
    .filter((file) => {
      try {
        const raw: unknown = JSON.parse(readFileSync(file, "utf8"));
        return typeof raw === "object" && raw !== null && (raw as { interim?: unknown }).interim === true;
      } catch {
        return false; // missing or unreadable: the page build reports it with the field at fault
      }
    })
    .map((file) => path.relative(path.dirname(root), file).split(path.sep).join("/"));
  if (interim.length) {
    throw new Error(
      `${interim.join(", ")} ${interim.length === 1 ? "is an interim placeholder" : "are interim placeholders"}, not an engine export. ` +
        "A production build ships real runs only: export one with `lull export-showcase` and copy it in with " +
        "`node scripts/sync-showcase.mjs <srcDir> <id>`.",
    );
  }
}

/**
 * Stops a production build that would publish test data as the evaluation: a
 * public/benchmarks.json marked `"fixture": true` (the e2e sample) never ships.
 */
export function assertRealBenchmarks(file: string): void {
  if (!existsSync(file)) return;
  let raw: unknown;
  try {
    raw = JSON.parse(readFileSync(file, "utf8"));
  } catch {
    return; // the benchmarks page build reports a file that doesn't read, with the field at fault
  }
  if (typeof raw === "object" && raw !== null && (raw as { fixture?: unknown }).fixture === true) {
    throw new Error(
      `${path.basename(path.dirname(file))}/${path.basename(file)} is a test fixture, not the published evaluation. ` +
        "A production build ships the evaluation's own results only (eval/ writes them).",
    );
  }
}
