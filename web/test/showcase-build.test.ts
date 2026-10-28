import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { PHASE_DEVELOPMENT_SERVER, PHASE_PRODUCTION_BUILD } from "next/constants";
import { afterEach, describe, expect, it, vi } from "vitest";
import { assertRealBenchmarks, assertRealShowcases } from "../lib/showcase-build";
import nextConfig, { SHOWCASE_ROOT } from "../next.config";

const REAL = readFileSync(path.join(__dirname, "..", "public", "showcase", "ed90", "showcase.json"), "utf8");

describe("a production build ships real runs only", () => {
  let root: string;
  afterEach(() => {
    if (root) rmSync(root, { recursive: true, force: true });
    vi.unstubAllEnvs();
  });

  const fixture = (shows: Record<string, string>) => {
    root = mkdtempSync(path.join(tmpdir(), "lull-guard-"));
    for (const [id, body] of Object.entries(shows)) {
      mkdirSync(path.join(root, id));
      writeFileSync(path.join(root, id, "showcase.json"), body);
    }
    return root;
  };

  it("fails on an interim showcase.json, naming it", () => {
    const interim = JSON.stringify({ ...JSON.parse(REAL), id: "draft", interim: true });
    const dir = fixture({ ed90: REAL, draft: interim });
    expect(() => assertRealShowcases(dir)).toThrow(/draft\/showcase\.json is an interim placeholder, not an engine export/);
    expect(() => assertRealShowcases(dir)).toThrow(/scripts\/sync-showcase\.mjs/);
  });

  it("passes engine exports", () => {
    expect(() => assertRealShowcases(fixture({ ed90: REAL }))).not.toThrow();
    expect(() => assertRealShowcases(path.join(tmpdir(), "lull-no-such-folder"))).not.toThrow();
  });

  it("is checked by next.config on a production build, against the site's own showcases", () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.lull.example");
    vi.stubEnv("NEXT_PUBLIC_SITE_URL", "https://lull.example");
    expect(SHOWCASE_ROOT).toBe(path.join(__dirname, "..", "public", "showcase"));
    // the shipped tree passes
    expect(nextConfig(PHASE_PRODUCTION_BUILD).poweredByHeader).toBe(false);
  });

  it("stops next.config's production build when an interim file is in the tree", () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.lull.example");
    vi.stubEnv("NEXT_PUBLIC_SITE_URL", "https://lull.example");
    const dir = path.join(SHOWCASE_ROOT, "zz-interim-guard-test");
    mkdirSync(dir, { recursive: true });
    try {
      writeFileSync(path.join(dir, "showcase.json"), JSON.stringify({ ...JSON.parse(REAL), id: "zz-interim-guard-test", interim: true }));
      expect(() => nextConfig(PHASE_PRODUCTION_BUILD)).toThrow(/zz-interim-guard-test\/showcase\.json is an interim placeholder/);
      // the dev server still starts, so the placeholder can be looked at
      expect(nextConfig(PHASE_DEVELOPMENT_SERVER).poweredByHeader).toBe(false);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});

describe("a production build ships the evaluation's own results only", () => {
  it("refuses a benchmarks.json marked as a test fixture, and passes a real one or none", () => {
    const dir = mkdtempSync(path.join(tmpdir(), "lull-bench-"));
    try {
      const file = path.join(dir, "benchmarks.json");
      expect(() => assertRealBenchmarks(file)).not.toThrow();
      writeFileSync(file, JSON.stringify({ fixture: true, generated_at: "x", git_commit: "y", suites: {}, known_failures: [] }));
      expect(() => assertRealBenchmarks(file)).toThrow(/is a test fixture, not the published evaluation/);
      // the e2e build, and only it, may be built from the fixture
      expect(() => assertRealBenchmarks(file, { allowFixture: true })).not.toThrow();
      writeFileSync(file, JSON.stringify({ generated_at: "x", git_commit: "y", suites: {}, known_failures: [] }));
      expect(() => assertRealBenchmarks(file)).not.toThrow();
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
