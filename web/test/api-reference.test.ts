import { existsSync, readFileSync, readdirSync } from "node:fs";
import { SNAPSHOT, apiSrc, readCodes } from "../scripts/api-codes.mjs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { API_REFUSALS } from "../lib/api";
import { API_REFERENCE, REFERENCE_ORDER } from "../lib/api-reference";
import { ROUTES, routePath } from "../lib/api-routes";
import { ATTRIBUTE_DOCS } from "../lib/player-reference";
import { PLAYER_ATTRIBUTES } from "../player/src/reference";

const LIB = path.join(__dirname, "..", "lib");

/** A source file without its comments, so only code is searched. */
const code = (file: string) =>
  readFileSync(file, "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:"'`])\/\/.*$/gm, "$1");

describe("the endpoint reference", () => {
  it("documents every route the client calls, and only those", () => {
    expect(Object.keys(API_REFERENCE).sort()).toEqual(Object.keys(ROUTES).sort());
    expect(new Set(REFERENCE_ORDER)).toEqual(new Set(Object.keys(ROUTES)));
  });

  it("the client builds every API address from the route table", () => {
    const own = new Set(["api-routes.ts", "api-reference.ts"]);
    const offenders = readdirSync(LIB)
      .filter((f) => /\.(ts|mjs)$/.test(f) && !own.has(f))
      .filter((f) => /["'`][^"'`\n]*\/v1\//.test(code(path.join(LIB, f))));
    expect(offenders).toEqual([]);
  });

  it("names every refusal code the API sends, and no other", () => {
    const documented = new Set(Object.values(API_REFERENCE).flatMap((d) => d.refusals.map((r) => r.code)));
    expect([...API_REFUSALS].filter((c) => !documented.has(c as never))).toEqual([]);
    expect([...documented].filter((c) => !API_REFUSALS.has(c))).toEqual([]);
  });

  it("every write that changes a run asks for the edit token", () => {
    for (const name of ["editCue", "approveCue", "holdCue", "publish"] as const) expect(API_REFERENCE[name].auth).toBe("edit-token");
    expect(API_REFERENCE.events.auth).toBe("edit-token to resume");
    // an unfinished run is shown in full only to its owner
    expect(API_REFERENCE.run.auth).toBe("edit-token to see an unfinished run");
    for (const name of ["health", "samples", "startRun", "listRuns", "original", "file", "cueAudio", "fork"] as const) expect(API_REFERENCE[name].auth).toBe("none");
  });

  it("each example calls its own route", () => {
    for (const [name, doc] of Object.entries(API_REFERENCE)) {
      const route = ROUTES[name as keyof typeof ROUTES];
      const pattern = route.path.replace(/\{[a-z_]+\}/g, "[^/\"?\\s]+").replace(/\//g, "\\/");
      expect(doc.example, name).toMatch(new RegExp(`\\$LULL_API${pattern}`));
      // a write says its method (curl's -F is a POST of its own)
      if (route.method !== "GET") expect(doc.example.includes(`-X ${route.method}`) || (route.method === "POST" && / -F /.test(doc.example)), name).toBe(true);
    }
  });
});

/** Every code the API raises, with its statuses: a snapshot of its source (scripts/api-codes.mjs). */
const SNAP = JSON.parse(readFileSync(SNAPSHOT, "utf8")) as { codes: Record<string, number[]> };
const API_SRC = apiSrc();

describe("the reference against the API's source", () => {
  it("every code the API raises is documented and known to the client, with the API's statuses", () => {
    const raised = new Map(Object.entries(SNAP.codes).map(([c, st]) => [c, new Set(st)]));
    expect(raised.size).toBeGreaterThan(10);
    const documented = new Map<string, Set<number>>();
    for (const d of Object.values(API_REFERENCE)) for (const r of d.refusals) documented.set(r.code, (documented.get(r.code) ?? new Set()).add(r.status));
    expect([...raised.keys()].filter((c) => !documented.has(c)), "raised by the API, not in the reference").toEqual([]);
    expect([...raised.keys()].filter((c) => !API_REFUSALS.has(c)), "raised by the API, unknown to lib/api.ts").toEqual([]);
    // each documented status is one the API raises that code with
    const wrong = [...documented].flatMap(([code, statuses]) => [...statuses].filter((st) => raised.has(code) && !raised.get(code)!.has(st)).map((st) => `${st} ${code}`));
    expect(wrong, "documented with a status the API never uses").toEqual([]);
  });

  it.skipIf(!existsSync(API_SRC))("the snapshot still matches the API's source (where it is checked out; LULL_API_SRC names it)", () => {
    expect(readCodes(API_SRC), "re-run node scripts/api-codes.mjs").toEqual(SNAP.codes);
  });

  it("the live start documents what the API says about long clips and its own storage", () => {
    const start = API_REFERENCE.startRun;
    expect(start.body?.fields.find((f) => f.name === "file")?.what).toMatch(/up to 60 s and 30 MB; longer clips are refused \(too_long\)/);
    expect(start.refusals).toContainEqual(expect.objectContaining({ status: 503, code: "storage", retryAfter: true }));
  });
});

describe("routePath", () => {
  it("fills and encodes the parameters, a file path segment by segment", () => {
    expect(routePath("run", { id: "live 1" })).toBe("/v1/runs/live%201");
    expect(routePath("file", { id: "r1", path: "keyframes/c 1.jpg" })).toBe("/v1/runs/r1/files/keyframes/c%201.jpg");
    expect(routePath("editCue", { id: "r1", cue_id: "c002" })).toBe("/v1/runs/r1/cues/c002");
    // a parameter left out keeps its placeholder (the player's per-cue pattern)
    expect(routePath("cueAudio", { id: "r1" })).toBe("/v1/runs/r1/cues/{cue_id}/audio");
  });
});

describe("the player reference", () => {
  it("documents every attribute the element observes, and only those", () => {
    expect(Object.keys(ATTRIBUTE_DOCS).sort()).toEqual([...PLAYER_ATTRIBUTES].sort());
  });
});
