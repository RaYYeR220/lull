import path from "node:path";

/**
 * The results file a build reads: public/benchmarks.json, unless the build names another with
 * LULL_BENCHMARKS_FILE (the e2e build names the test fixture; next.config refuses a fixture
 * in any other build). Relative paths are from the site's folder.
 */
export function benchmarksFile(env: Record<string, string | undefined>, root = process.cwd()): string {
  const named = env.LULL_BENCHMARKS_FILE?.trim();
  return path.resolve(root, named || path.join("public", "benchmarks.json"));
}
