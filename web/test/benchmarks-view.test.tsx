import { readFileSync } from "node:fs";
import path from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { BenchmarksView } from "../components/benchmarks/BenchmarksView";
import { parseBenchmarks } from "../lib/benchmarks-schema";

const FIXTURE = parseBenchmarks(JSON.parse(readFileSync(path.join(__dirname, "..", "e2e", "fixtures", "benchmarks.sample.json"), "utf8")));

describe("the benchmarks view, decided at build time", () => {
  it("with no published file: the empty state, and nothing fetched in the browser", () => {
    const html = renderToStaticMarkup(<BenchmarksView data={null} readme="https://example.org/eval/README.md" empty={<p>The evaluation hasn’t been published yet.</p>} />);
    expect(html).toContain("The evaluation hasn’t been published yet.");
    expect(html).not.toContain("data-measure");
  });

  it("with the fixture: every view says it is test data", () => {
    const html = renderToStaticMarkup(<BenchmarksView data={FIXTURE} readme="https://example.org/eval/README.md" empty={null} />);
    expect(html).toContain("data-fixture");
    expect(html).toContain("Test fixture.");
    expect(html).toContain('data-measure="recall_standard"');
  });
});
