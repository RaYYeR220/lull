import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { forgetRun, ownRuns, rememberRun } from "../lib/own-runs";

/** A localStorage stand-in, and one that throws (private mode, blocked storage). */
function storage(throws = false) {
  const m = new Map<string, string>();
  return {
    getItem: (k: string) => {
      if (throws) throw new Error("blocked");
      return m.get(k) ?? null;
    },
    setItem: (k: string, v: string) => {
      if (throws) throw new Error("blocked");
      m.set(k, v);
    },
    removeItem: (k: string) => m.delete(k),
  };
}

const DAY = 86_400_000;

describe("this browser's own runs", () => {
  beforeEach(() => vi.stubGlobal("window", { localStorage: storage() }));
  afterEach(() => vi.unstubAllGlobals());

  it("are kept newest first, one row per run, with its title and kind", () => {
    rememberRun({ id: "live-a", title: "ed45.mp4", kind: "sample", made: 1000 });
    rememberRun({ id: "live-b", title: "lecture.mp4", kind: "upload", made: 2000 });
    rememberRun({ id: "live-a", title: "ed45.mp4", kind: "sample", made: 3000 });
    expect(ownRuns(3000).map((r) => r.id)).toEqual(["live-a", "live-b"]);
  });

  it("drop what the service has removed: uploads after 7 days, copies after 24 hours (or what it says)", () => {
    const now = 10 * DAY;
    rememberRun({ id: "up-old", title: "a.mp4", kind: "upload", made: now - 8 * DAY });
    rememberRun({ id: "up-new", title: "b.mp4", kind: "upload", made: now - 6 * DAY });
    rememberRun({ id: "fork-old", title: "Copy of ed45", kind: "fork", made: now - 25 * 3_600_000 });
    rememberRun({ id: "fork-new", title: "Copy of ed45", kind: "fork", made: now - 2 * 3_600_000 });
    rememberRun({ id: "sample-old", title: "ed45.mp4", kind: "sample", made: now - 30 * DAY });
    expect(ownRuns(now).map((r) => r.id).sort()).toEqual(["fork-new", "sample-old", "up-new"]);
    expect(ownRuns(now, { uploadDays: 5, forkHours: 1 }).map((r) => r.id)).toEqual(["sample-old"]);
  });

  it("keep at most 20, and can forget one", () => {
    for (let i = 0; i < 25; i++) rememberRun({ id: `live-${i}`, title: `${i}.mp4`, kind: "sample", made: i });
    expect(ownRuns(100)).toHaveLength(20);
    forgetRun("live-24");
    expect(ownRuns(100)[0]!.id).toBe("live-23");
  });

  it("read nothing, and break nothing, when storage is blocked or holds junk", () => {
    vi.stubGlobal("window", { localStorage: storage(true) });
    expect(() => rememberRun({ id: "x", title: "x", kind: "upload", made: 1 })).not.toThrow();
    expect(ownRuns(1)).toEqual([]);
    const junk = storage();
    junk.setItem("lull-own-runs", "{not json");
    vi.stubGlobal("window", { localStorage: junk });
    expect(ownRuns(1)).toEqual([]);
    junk.setItem("lull-own-runs", JSON.stringify([{ id: 3 }, { id: "ok", title: "t", kind: "sample", made: 1 }, { id: "bad kind", title: "t", kind: "x", made: 1 }]));
    expect(ownRuns(1).map((r) => r.id)).toEqual(["ok"]);
  });
});
