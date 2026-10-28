import { describe, expect, it } from "vitest";
import { createAnnouncer, type CueNote } from "../lib/announce";

/** A fake clock and timer queue, so the throttle can be stepped through. */
function harness(gap = 2000) {
  let now = 0;
  const said: [number, string][] = [];
  const timers: { at: number; fn: () => void; id: number }[] = [];
  let ids = 0;
  const a = createAnnouncer((t) => said.push([now, t]), {
    gap,
    clock: () => now,
    schedule: (fn, ms) => {
      const id = ++ids;
      timers.push({ at: now + ms, fn, id });
      return id;
    },
    cancel: (id) => {
      const i = timers.findIndex((t) => t.id === id);
      if (i >= 0) timers.splice(i, 1);
    },
  });
  const advance = (ms: number) => {
    const end = now + ms;
    for (;;) {
      timers.sort((x, y) => x.at - y.at);
      const next = timers[0];
      if (!next || next.at > end) break;
      timers.shift();
      now = next.at;
      next.fn();
    }
    now = end;
  };
  return { a, said, advance };
}

const cue = (n: number, status: CueNote["status"] = "verified", extra: Partial<CueNote> = {}): CueNote => ({
  n,
  extended: false,
  status,
  text: `Line ${n}.`,
  note: null,
  start: n * 10,
  ...extra,
});

describe("createAnnouncer", () => {
  it("reads one cue in full, with its time", () => {
    const { a, said, advance } = harness();
    a.cue(cue(3));
    advance(0);
    expect(said).toEqual([[0, "Cue 3 verified at 0:30: Line 3."]]);
  });

  it("says why a cue was held", () => {
    const { a, said, advance } = harness();
    a.cue(cue(2, "held", { note: "the red door is not visible" }));
    advance(0);
    expect(said[0]![1]).toBe("Cue 2 held: the red door is not visible.");
  });

  it("never speaks twice within the gap: what arrives in between is merged", () => {
    const { a, said, advance } = harness();
    a.stage("Listening for speech and pauses.");
    advance(0);
    a.stage("Watching the frames.");
    a.stage("Drafting lines for the pauses.");
    a.cue(cue(1));
    a.cue(cue(2, "held"));
    a.cue(cue(3));
    advance(1999);
    expect(said).toHaveLength(1);
    advance(1);
    expect(said).toEqual([
      [0, "Listening for speech and pauses."],
      [2000, "Drafting lines for the pauses. 3 lines checked: 2 verified, 1 held."],
    ]);
  });

  it("says the end at once and drops what was waiting", () => {
    const { a, said, advance } = harness();
    a.stage("Watching the frames.");
    advance(0);
    a.cue(cue(1));
    a.now("Finished. 1 line passed.");
    advance(5000);
    expect(said.map((s) => s[1])).toEqual(["Watching the frames.", "Finished. 1 line passed."]);
  });

  it("names extended lines apart", () => {
    const { a, said, advance } = harness();
    a.cue(cue(1, "extended", { extended: true }));
    advance(0);
    expect(said[0]![1]).toBe("Extended line 1 ready at 0:10: Line 1.");
  });
});
