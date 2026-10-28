import { readFile } from "node:fs/promises";
import { cache } from "react";
import { benchmarksFile } from "./benchmarks-file";
import { parseBenchmarks, type Benchmarks } from "./benchmarks-schema";

export { BENCHMARK_KEYS, landingMetrics, type BenchmarkKey, type LandingCell } from "./benchmarks-landing";

/**
 * The evaluation's published results (public/benchmarks.json, or the file the build names),
 * checked (lib/benchmarks-schema.ts), or null while there is none. Read at build time only:
 * the page knows then whether there are results. A file that doesn't hold fails the build.
 */
export const loadBenchmarks = cache(async (): Promise<Benchmarks | null> => {
  let text: string;
  try {
    text = await readFile(benchmarksFile(process.env), "utf8");
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw e;
  }
  return parseBenchmarks(JSON.parse(text));
});
