import { spawnSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  MAX_480_BYTES,
  MAX_DESCRIBED_BYTES,
  MAX_POSTER_BYTES,
  MAX_VIDEO_BYTES,
  audioArgs,
  isInside,
  posterArgs,
  reencodeArgs,
  syncShowcase,
  writeIndex,
  type MediaTools,
} from "../scripts/sync-showcase.mjs";

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
    expect(index).toEqual([{ id: "ed90", title: "Elephants Dream", credit: REAL.credit, duration: 90, cues: 13, held: 1, costUsd: 0.038668 }]);
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

  it("refuses a file reached through a linked folder outside the export, leaving the site's copy as it was", () => {
    syncShowcase({ src: makeExport("first"), id: "ed90", dest });
    const before = readFileSync(path.join(dest, "ed90", "audio", "c001.m4a"));
    const src = makeExport("linked");
    // the export's audio/ is a link (a junction on Windows) to a folder elsewhere on the disk
    const elsewhere = path.join(work, "elsewhere");
    cpSync(path.join(src, "audio"), elsewhere, { recursive: true });
    writeFileSync(path.join(elsewhere, "c001.m4a"), "a file from outside the export");
    rmSync(path.join(src, "audio"), { recursive: true });
    symlinkSync(elsewhere, path.join(src, "audio"), "junction");
    expect(() => syncShowcase({ src, id: "ed90", dest })).toThrow(/audio\/\w+\.m4a resolves outside .*linked through a linked folder/);
    expect(readFileSync(path.join(dest, "ed90", "audio", "c001.m4a"))).toEqual(before);
  });

  it("tells inside from outside by whole path segments", () => {
    const root = path.join(work, "export");
    expect(isInside(root, root)).toBe(true);
    expect(isInside(root, path.join(root, "audio", "c001.m4a"))).toBe(true);
    // a folder whose name merely starts with two dots is still inside
    expect(isInside(root, path.join(root, "..cache", "c001.m4a"))).toBe(true);
    expect(isInside(root, path.join(work, "export-2", "c001.m4a"))).toBe(false);
    expect(isInside(root, path.join(work, "c001.m4a"))).toBe(false);
    expect(isInside(root, work)).toBe(false);
  });

  /** Stand-in media steps: each writes `size` bytes and records what it was asked. */
  function fakeTools(sizes: { video480?: number; video360?: number; audio?: number; poster?: number | "fail" } = {}) {
    const calls: string[] = [];
    const tools: MediaTools = {
      video: (from, to, lines, sound) => {
        calls.push(`video ${path.basename(from)} ${lines} ${sound}`);
        writeFileSync(to, Buffer.alloc(lines === 480 ? (sizes.video480 ?? 2) : (sizes.video360 ?? 1), 7));
      },
      audio: (from, to) => {
        calls.push(`audio ${path.basename(from)}`);
        writeFileSync(to, Buffer.alloc(sizes.audio ?? 2, 8));
      },
      poster: (from, to) => {
        calls.push(`poster ${path.basename(from)}`);
        if (sizes.poster === "fail") throw new Error("no frame at 1 s");
        writeFileSync(to, Buffer.alloc(sizes.poster ?? 3, 9));
      },
    };
    return { tools, calls };
  }

  it("leaves small media as it is and adds a poster", () => {
    const { tools, calls } = fakeTools();
    const r = syncShowcase({ src: makeExport("small"), id: "ed90", dest, maxVideoBytes: 4, maxDescribedBytes: 4, tools });
    expect(calls).toEqual(["poster video.mp4"]);
    expect(r.video).toEqual({ bytes: 4, reencoded: false });
    expect(r.described).toBeNull();
    expect(r.poster).toEqual({ bytes: 3 });
    expect(listed(path.join(dest, "ed90"))).toEqual([...r.files, "poster.jpg"].sort());
  });

  it("re-encodes a video over the limit at 480 lines, and again at 360 when that is still too big", () => {
    const at480 = fakeTools({ video480: 2 });
    const a = syncShowcase({ src: makeExport("big"), id: "ed90", dest, maxVideoBytes: 3, max480Bytes: 2, tools: at480.tools });
    expect(at480.calls).toEqual(["video video.mp4 480 copy", "poster video.mp4"]);
    expect(a.video).toEqual({ bytes: 2, reencoded: true, from: 4, lines: 480 });

    const at360 = fakeTools({ video480: 3, video360: 1 });
    const b = syncShowcase({ src: makeExport("bigger"), id: "ed90", dest, maxVideoBytes: 3, max480Bytes: 2, tools: at360.tools });
    // made from the export again, not from the 480-line copy; the sound as AAC at 64 kbit/s
    expect(at360.calls).toEqual(["video video.mp4 480 copy", "video video.mp4 360 aac64", "poster video.mp4"]);
    expect(b.video).toEqual({ bytes: 1, reencoded: true, from: 4, lines: 360 });
    expect(readFileSync(path.join(dest, "ed90", "video.mp4"))).toEqual(Buffer.alloc(1, 7));
  });

  it("re-encodes a described mix over its limit", () => {
    const { tools, calls } = fakeTools({ audio: 2 });
    const r = syncShowcase({ src: makeExport("mix"), id: "ed90", dest, maxDescribedBytes: 3, tools });
    expect(calls).toEqual(["audio described-audio.m4a", "poster video.mp4"]);
    expect(r.described).toEqual({ bytes: 2, from: 4 });
    expect(readFileSync(path.join(dest, "ed90", "described-audio.m4a"))).toEqual(Buffer.alloc(2, 8));
  });

  it("goes on without a poster it can't make, and says why", () => {
    const { tools } = fakeTools({ poster: "fail" });
    const r = syncShowcase({ src: makeExport("still"), id: "ed90", dest, tools });
    expect(r.poster).toEqual({ error: "no frame at 1 s" });
    expect(existsSync(path.join(dest, "ed90", "poster.jpg"))).toBe(false);
  });

  it("keeps the site's copy when re-encoding fails", () => {
    syncShowcase({ src: makeExport("before"), id: "ed90", dest, tools: fakeTools().tools });
    const fail: MediaTools = {
      ...fakeTools().tools,
      video: () => {
        throw new Error("no ffmpeg");
      },
    };
    expect(() => syncShowcase({ src: makeExport("after"), id: "ed90", dest, maxVideoBytes: 1, tools: fail })).toThrow(/no ffmpeg/);
    expect(readFileSync(path.join(dest, "ed90", "video.mp4"))).toEqual(Buffer.from([0, 1, 2, 9]));
  });

  it("re-encodes with CRF 30 and keeps the timing: the sound copied at 480 lines, AAC 64k at 360", () => {
    const args = reencodeArgs("in.mp4", "out.mp4");
    expect(args).toEqual(expect.arrayContaining(["-i", "in.mp4", "out.mp4", "scale=-2:'min(480,ih)'", "libx264", "+faststart"]));
    expect(args[args.indexOf("-crf") + 1]).toBe("30");
    expect(args[args.indexOf("-c:a") + 1]).toBe("copy");
    const small = reencodeArgs("in.mp4", "out.mp4", 360, "aac64");
    expect(small).toEqual(expect.arrayContaining(["scale=-2:'min(360,ih)'", "aac", "64k"]));
    expect(audioArgs("a.m4a", "b.m4a")).toEqual(expect.arrayContaining(["-vn", "aac", "64k"]));
    expect(posterArgs("v.mp4", "p.jpg", 5)).toEqual(expect.arrayContaining(["-ss", "1", "-frames:v", "1", "-q:v", "5"]));
    expect([MAX_VIDEO_BYTES, MAX_480_BYTES, MAX_DESCRIBED_BYTES, MAX_POSTER_BYTES]).toEqual([15 * 1024 * 1024, 25 * 1024 * 1024, 8 * 1024 * 1024, 60 * 1024]);
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
    const src = makeExport("short", undefined, { skip: ["audio/c008.m4a", "extended.vtt"] });
    expect(() => syncShowcase({ src, id: "ed90", dest })).toThrow(/missing audio\/c008\.m4a, extended\.vtt|missing extended\.vtt, audio\/c008\.m4a/);
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
