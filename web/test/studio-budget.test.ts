import { describe, expect, it } from "vitest";
import { budget, budgetWords, countWords, inferWps } from "../lib/studio-budget";

describe("countWords: the engine's spoken words", () => {
  it("counts whitespace tokens that hold a letter or digit", () => {
    expect(countWords("The two people are surrounded by wires.")).toBe(7);
    expect(countWords("  Version 2.1 — out now  ")).toBe(4); // the dash says nothing
    expect(countWords("Emo’s hand, then Proog’s.")).toBe(4);
    expect(countWords("Ça va, 東京!")).toBe(3);
    expect(countWords("")).toBe(0);
    expect(countWords(" … — ")).toBe(0);
  });
});

describe("inferWps: the voice's rate behind the capacities", () => {
  it("finds the rate every pause agrees on (floor(seconds × wps) = capacity)", () => {
    const wps = 3.05;
    const gaps = [6.54, 5.06, 4.575, 1.431, 3.221].map((s, i) => ({ start: 10 * i, end: 10 * i + s, capacity: Math.floor(s * wps + 1e-9) }));
    const got = inferWps(gaps)!;
    expect(got.low).toBeLessThanOrEqual(wps);
    expect(got.high).toBeGreaterThan(wps);
    expect(Math.abs(got.wps - wps)).toBeLessThan(0.06);
  });

  it("gives up without usable pauses or when the capacities disagree", () => {
    expect(inferWps([])).toBeNull();
    expect(inferWps([{ start: 1, end: 1, capacity: 3 }])).toBeNull();
    expect(
      inferWps([
        { start: 0, end: 2, capacity: 10 }, // 5-5.5 wps
        { start: 5, end: 7, capacity: 2 }, // 1-1.5 wps
      ]),
    ).toBeNull();
  });
});

describe("budget: a line against its pause", () => {
  const gap = { start: 9, end: 15.54, capacity: 19 };

  it("counts what's left and how long it takes to say", () => {
    const b = budget("A second person in a light outfit joins the bald person in a doorway.", gap, 3.05);
    expect(b).toMatchObject({ words: 14, capacity: 19, left: 5, over: false });
    expect(b.room).toBeCloseTo(6.54, 6);
    expect(b.seconds).toBeCloseTo(14 / 3.05, 6);
    expect(b.fill).toBeCloseTo(14 / 19, 6);
    expect(budgetWords(b)).toBe("14 of 19 words");
  });

  it("says when a line runs over", () => {
    const b = budget(Array.from({ length: 22 }, (_, i) => `w${i}`).join(" "), gap, 3.05);
    expect(b).toMatchObject({ words: 22, left: -3, over: true });
    expect(budgetWords(b)).toBe("3 words over: 22 of 19");
    expect(budgetWords(budget("one two three four", { start: 0, end: 1, capacity: 3 }, null))).toBe("1 word over: 4 of 3");
  });

  it("a line without a pause (extended) has no capacity, only its words and length", () => {
    const b = budget("A room.", null, 2);
    expect(b).toEqual({ words: 2, capacity: null, left: null, over: false, room: null, seconds: 1, fill: null });
    expect(budgetWords(b)).toBe("2 words");
    expect(budget("A room.", null, null).seconds).toBeNull();
  });

  it("a pause that holds nothing is full at the first word", () => {
    expect(budget("", { start: 0, end: 0.2, capacity: 0 }, 3).fill).toBe(0);
    expect(budget("Hi", { start: 0, end: 0.2, capacity: 0 }, 3)).toMatchObject({ over: true, fill: Infinity });
  });
});
