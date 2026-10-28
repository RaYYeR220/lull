import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { syncShowcase, writeIndex } from "../scripts/sync-showcase.mjs";

const SCRIPT = path.join(__dirname, "..", "scripts", "sync-showcase.mjs");
const REAL = JSON.parse(readFileSync(path.join(__dirname, "..", "public", "showcase", "ed90", "showcase.json"), "utf8")) as Record<string, unknown>;

let work: string;
let dest: string;

beforeEach(() => {
  work = mkdtempSync(path.join(tmpdir(), "lull-sync-"));
  dest = path.join(work, "public", "showcase");
});
afterEach(() => {
  rmSync(work, { recursive: true, force: true });
});

type Json = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

/** A showcase export in the engine's layout, with stand-in bytes for the media. */
function makeExport(name: string, edit: (j: Json) => void = () => {}, { skip = [] as string[], extra = [] as string[] } = {}) {
  const dir = path.join(work, name);
  const json = structuredClone(REAL) as Json;
  edit(json);
  const put = (rel: string, body: string | Buffer) => {
    if (skip.includes(rel)) return;
    const file = path.join(dir, ...rel.split("/"));
    mkdirSync(path.dirname(file), { recursive: true });
    writeFileSync(file, body);
  };
  put("showcase.json", JSON.stringify(json));
  for (const f of ["video.mp4", "described-audio.m4a"]) put(f, Buffer.from([0, 1, 2, f.length]));
  put("descriptions.vtt", "WEBVTT\n");
  put("extended.vtt", "WEBVTT\n");
  for (const c of json.cues as { id: string }[]) {
    put(`audio/${c.id}.m4a`, Buffer.from([9, 9]));
    put(`keyframes/${c.id}.jpg`, Buffer.from([0xff, 0xd8]));
  }
  for (const f of extra) put(f, "not for the site");
  return dir;
}

const listed = (dir: string): string[] =>
  readdirSync(dir, { withFileTypes: true, recursive: true })
    .filter((d) => d.isFile())
    .map((d) => path.relative(dir, path.join(d.parentPath, d.name)).split(path.sep).join("/"))
    .sort();

describe("sync-showcase", () => {
  it("copies the files the site plays, showcase.json untouched, and lists the run in the index", () => {
    const src = makeExport("ed90", undefined, { extra: ["report.html", "notes.txt", "audio/c999.m4a", "keyframes/c999.jpg"] });
    const { files, index } = syncShowcase({ src, id: "ed90", dest });
    const copied = listed(path.join(dest, "ed90"));
    expect(copied).toEqual(files);
    expect(copied).toContain("showcase.json");
    expect(copied).toContain("video.mp4");
    expect(copied).toContain("audio/x002.m4a");
    expect(copied).toContain("keyframes/c013.jpg");
    // nothing the site doesn't play
    for (const stray of ["report.html", "notes.txt", "audio/c999.m4a", "keyframes/c999.jpg"]) expect(copied).not.toContain(stray);
    expect(readFileSync(path.join(dest, "ed90", "showcase.json"))).toEqual(readFileSync(path.join(src, "showcase.json")));
    expect(index).toEqual([{ id: "ed90", title: "Elephants Dream", credit: REAL.credit, duration: 90, cues: 10, costUsd: 0.03644 }]);
    expect(JSON.parse(readFileSync(path.join(dest, "index.json"), "utf8"))).toEqual(index);
  });

  it("replaces an earlier copy and keeps the index's order, adding a new run at the end", () => {
    syncShowcase({ src: makeExport("first"), id: "ed90", dest });
    writeFileSync(path.join(dest, "ed90", "left-over.txt"), "old");
    syncShowcase({ src: makeExport("bbb", (j) => Object.assign(j, { id: "bbb", title: "Big Buck Bunny", costUsd: 0.05 })), id: "bbb", dest });
    syncShowcase({ src: makeExport("again", (j) => (j.title = "Elephants Dream (again)")), id: "ed90", dest });
    expect(existsSync(path.join(dest, "ed90", "left-over.txt"))).toBe(false);
    const index = JSON.parse(readFileSync(path.join(dest, "index.json"), "utf8")) as { id: string; title: string }[];
    expect(index.map((e) => [e.id, e.title])).toEqual([
      ["ed90", "Elephants Dream (again)"],
      ["bbb", "Big Buck Bunny"],
    ]);
    // a folder that is gone leaves the index
    rmSync(path.join(dest, "bbb"), { recursive: true });
    expect(writeIndex(dest).map((e) => e.id)).toEqual(["ed90"]);
  });

  it("refuses absolute paths in showcase.json and leaves the site's copy as it was", () => {
    syncShowcase({ src: makeExport("good"), id: "ed90", dest });
    const before = listed(path.join(dest, "ed90"));
    for (const [field, value] of [
      ["original", "C:/Users/someone/lull-runs/showcase/ed90/video.mp4"],
      ["original", "/var/runs/ed90/video.mp4"],
      ["described", "\\\\box\\share\\described-audio.m4a"],
    ] as const) {
      const src = makeExport(`abs-${field}-${value.length}`, (j) => (j.audio[field] = value));
      expect(() => syncShowcase({ src, id: "ed90", dest })).toThrow(new RegExp(`audio\\.${field} is an absolute path`));
    }
    const src = makeExport("abs-base", (j) => (j.cueAudioBase = "C:\\export\\audio\\"));
    expect(() => syncShowcase({ src, id: "ed90", dest })).toThrow(/cueAudioBase is an absolute path/);
    expect(listed(path.join(dest, "ed90"))).toEqual(before);
  });

  it("refuses paths that climb out of the export", () => {
    const src = makeExport("climb", (j) => (j.audio.original = "../ed90.mp4"));
    expect(() => syncShowcase({ src, id: "ed90", dest })).toThrow(/audio\.original must be a plain path/);
  });

  it("refuses an interim placeholder", () => {
    const src = makeExport("interim", (j) => (j.interim = true));
    expect(() => syncShowcase({ src, id: "ed90", dest })).toThrow(/interim placeholder, not an engine export/);
    expect(existsSync(path.join(dest, "ed90"))).toBe(false);
  });

  it("refuses an export whose id isn't the one asked for, or a bad id", () => {
    expect(() => syncShowcase({ src: makeExport("x"), id: "bbb", dest })).toThrow(/id is ed90, expected bbb/);
    expect(() => syncShowcase({ src: makeExport("y"), id: "../ed90", dest })).toThrow(/lowercase letters/);
  });

  it("names the files the player needs that the export lacks", () => {
    const src = makeExport("short", undefined, { skip: ["audio/c007.m4a", "extended.vtt"] });
    expect(() => syncShowcase({ src, id: "ed90", dest })).toThrow(/missing audio\/c007\.m4a, extended\.vtt|missing extended\.vtt, audio\/c007\.m4a/);
    expect(existsSync(path.join(dest, "ed90"))).toBe(false);
  });

  it("refuses a folder that isn't an export", () => {
    const empty = path.join(work, "empty");
    mkdirSync(empty);
    expect(() => syncShowcase({ src: empty, id: "ed90", dest })).toThrow(/no showcase\.json/);
    expect(() => syncShowcase({ src: path.join(work, "nowhere"), id: "ed90", dest })).toThrow(/is not a folder/);
  });

  it("runs from the command line: usage, and the reason on failure", () => {
    const usage = spawnSync(process.execPath, [SCRIPT], { encoding: "utf8" });
    expect(usage.status).toBe(2);
    expect(usage.stderr).toMatch(/usage: node scripts\/sync-showcase\.mjs <srcDir> <id>/);
    const bad = spawnSync(process.execPath, [SCRIPT, makeExport("cli", (j) => (j.audio.original = "/abs/video.mp4")), "ed90"], { encoding: "utf8" });
    expect(bad.status).toBe(1);
    expect(bad.stderr).toMatch(/sync-showcase: showcase ed90: audio\.original is an absolute path/);
  });
});
