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

describe("the landing's benchmark cells", () => {
  it("show each figure's detail as text, not only in a tooltip", async () => {
    const { Benchmarks } = await import("../components/landing/Benchmarks");
    const { parseShowcase } = await import("../lib/showcase-schema.mjs");
    const raw = JSON.parse(readFileSync(path.join(__dirname, "..", "e2e", "fixtures", "benchmarks.sample.json"), "utf8"));
    delete raw.fixture;
    raw.suites.suite_a.complete = true;
    raw.suites.suite_a.note = null;
    const show = parseShowcase(JSON.parse(readFileSync(path.join(__dirname, "..", "public", "showcase", "ed90", "showcase.json"), "utf8")), "ed90");
    const html = renderToStaticMarkup(<Benchmarks benchmarks={parseBenchmarks(raw)} showcase={show} />);
    expect(html).toContain("3 of 41 visual facts, standard track; 95 % interval 5.9–8.3 %.");
    expect(html).not.toContain('title="');
  });
});
