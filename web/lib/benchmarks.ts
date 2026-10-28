import { readFile } from "node:fs/promises";
import path from "node:path";
import { cache } from "react";
import { parseBenchmarks, type Benchmarks } from "./benchmarks-schema";

export { BENCHMARK_KEYS, landingMetrics, type BenchmarkKey, type LandingCell } from "./benchmarks-landing";

/** Where the evaluation's published results live in the site. */
export const BENCHMARKS_FILE = path.join("public", "benchmarks.json");

/**
 * public/benchmarks.json as the evaluation published it, checked (lib/benchmarks-schema.ts),
 * or null while it doesn't exist. A file that doesn't hold fails the build.
 */
export const loadBenchmarks = cache(async (): Promise<Benchmarks | null> => {
  let text: string;
  try {
    text = await readFile(path.join(process.cwd(), BENCHMARKS_FILE), "utf8");
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw e;
  }
  return parseBenchmarks(JSON.parse(text));
});
