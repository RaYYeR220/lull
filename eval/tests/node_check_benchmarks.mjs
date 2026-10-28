// Runs the website's own benchmarks.json validator (lull-web: web/lib/benchmarks-schema.ts,
// loaded through node's TypeScript type stripping) on a file, for tests/test_report.py.
//   node node_check_benchmarks.mjs <path to benchmarks-schema.ts> <benchmarks.json>
// Prints one JSON line: {ok, suiteA, other, failures, fixture} or {ok: false, error}; exit 1 on error.
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const [schema, file] = process.argv.slice(2);
const mod = await import(pathToFileURL(schema).href);
try {
  const b = mod.parseBenchmarks(JSON.parse(readFileSync(file, "utf8")));
  console.log(JSON.stringify({
    ok: true,
    suiteA: b.suiteA ? b.suiteA.videos.length : null,
    other: b.otherSuites,
    failures: b.knownFailures.length,
    fixture: b.fixture,
  }));
} catch (e) {
  console.log(JSON.stringify({ ok: false, error: String(e && e.message) }));
  process.exit(1);
}
