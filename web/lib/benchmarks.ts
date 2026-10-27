import { readFile } from "node:fs/promises";
import path from "node:path";
import { cache } from "react";

export const BENCHMARK_KEYS = ["overlap", "hallucination", "recall", "costPerMinute"] as const;
export type BenchmarkKey = (typeof BENCHMARK_KEYS)[number];

/**
 * public/benchmarks.json, written by the evaluation run. A metric that is missing has not
 * been measured, and the page says so; nothing is ever filled in by hand.
 */
export type Benchmarks = {
  /** Each measured metric as it should read, e.g. { value: "0.00 s" }. */
  metrics: Partial<Record<BenchmarkKey, { value: string; detail?: string }>>;
};

/** The published numbers, or null while public/benchmarks.json does not exist. */
export const loadBenchmarks = cache(async (): Promise<Benchmarks | null> => {
  const file = path.join(process.cwd(), "public", "benchmarks.json");
  let text: string;
  try {
    text = await readFile(file, "utf8");
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw e;
  }
  const raw: unknown = JSON.parse(text);
  const metrics = (raw as { metrics?: unknown })?.metrics;
  if (typeof metrics !== "object" || metrics === null || Array.isArray(metrics)) throw new Error("benchmarks.json: metrics must be an object");
  const out: Benchmarks["metrics"] = {};
  for (const key of BENCHMARK_KEYS) {
    const m = (metrics as Record<string, unknown>)[key];
    if (m === undefined) continue;
    const v = m as { value?: unknown; detail?: unknown };
    if (typeof v.value !== "string" || !v.value) throw new Error(`benchmarks.json: metrics.${key}.value must be a non-empty string`);
    out[key] = { value: v.value, ...(typeof v.detail === "string" ? { detail: v.detail } : {}) };
  }
  return { metrics: out };
});
