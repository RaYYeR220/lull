import { describe, expect, it } from "vitest";
import { MAX_PPS, MIN_PPS, ZOOM_STEPS, block, declutter, clampPps, fitPps, neighbour, rulerTicks, scrollToShow, stepPps, timeToX, waveHeights, xToTime, zoomScroll } from "../lib/studio-geometry";

describe("zoom", () => {
  it("fits the whole clip into the view, within the zoom's range", () => {
    expect(fitPps(900, 90)).toBe(10);
    expect(fitPps(100_000, 10)).toBe(MAX_PPS);
    expect(fitPps(200, 600)).toBe(MIN_PPS / 2); // a long clip on a phone: still something to see
    expect(fitPps(0, 90)).toBe(MIN_PPS);
    expect(fitPps(800, 0)).toBe(MIN_PPS);
  });

  it("steps in and out through the steps, from anywhere between them", () => {
    expect(stepPps(8, 1)).toBe(12);
    expect(stepPps(8.67, 1)).toBe(12);
    expect(stepPps(8.67, -1)).toBe(8);
    expect(stepPps(MAX_PPS, 1)).toBe(MAX_PPS);
    expect(stepPps(MIN_PPS, -1)).toBe(MIN_PPS);
    expect(stepPps(3, -1)).toBe(3); // already under the smallest step (a fitted long clip)
    expect(ZOOM_STEPS.every((z, i) => i === 0 || z > ZOOM_STEPS[i - 1]!)).toBe(true);
    expect(clampPps(1000)).toBe(MAX_PPS);
    expect(clampPps(1)).toBe(MIN_PPS);
  });

  it("keeps the moment under the anchor still while zooming", () => {
    // at 10 px/s, scrolled 300 px, the anchor 200 px in is at 50 s
    const next = zoomScroll(300, 200, 10, 20, 800, 90);
    expect((next + 200) / 20).toBeCloseTo(50, 9);
    // clamped at the start and at the end of the clip
    expect(zoomScroll(0, 0, 10, 20, 800, 90)).toBe(0);
    expect(zoomScroll(100, 700, 10, 6, 800, 90)).toBe(0); // the whole clip fits: no scroll
    expect(zoomScroll(1000, 800, 20, 40, 800, 90)).toBe(90 * 40 - 800);
  });
});

describe("placing things on the clock", () => {
  it("time and pixels go both ways, clamped to the clip", () => {
    expect(timeToX(12.5, 8)).toBe(100);
    expect(xToTime(100, 8, 90)).toBe(12.5);
    expect(xToTime(-5, 8, 90)).toBe(0);
    expect(xToTime(10_000, 8, 90)).toBe(90);
  });

  it("a block is as long as its span, and never thinner than it can be seen", () => {
    expect(block(10, 12.5, 8)).toEqual({ left: 80, width: 20 });
    expect(block(10, 10.2, 8, 14)).toEqual({ left: 80, width: 14 });
    expect(block(10, null, 8, 14)).toEqual({ left: 80, width: 14 }); // a held line has no take
  });

  it("scrolls as little as it can to show a span, with room around it", () => {
    expect(scrollToShow(100, 140, 0, 800)).toBe(0); // in view: stays
    expect(scrollToShow(900, 940, 0, 800, 48)).toBe(940 + 48 - 800);
    expect(scrollToShow(100, 140, 400, 800, 48)).toBe(52);
    expect(scrollToShow(20, 40, 400, 800, 48)).toBe(0);
    expect(scrollToShow(100, 2000, 0, 800, 48)).toBe(52); // wider than the view: from its start
  });

  it("labels the ruler no closer than the gap, at round steps", () => {
    expect(rulerTicks(90, 8)).toEqual({ step: 10, ticks: [0, 10, 20, 30, 40, 50, 60, 70, 80, 90] });
    expect(rulerTicks(30, 64).step).toBe(2);
    expect(rulerTicks(90, 3).step).toBe(30);
    expect(rulerTicks(7, 128).ticks).toEqual([0, 1, 2, 3, 4, 5, 6, 7]);
  });

  it("the waveform's strokes grow with loudness, a hair at silence", () => {
    const [silent, quiet, loud, louder] = waveHeights([-120, -40, -16, 0], 18);
    expect(silent).toBeCloseTo(0.4, 6);
    expect(quiet!).toBeGreaterThan(silent!);
    expect(loud).toBeCloseTo(18.4, 6);
    expect(louder).toBeCloseTo(18.4, 6);
  });
});

describe("labels that would run into each other", () => {
  it("shows a label only where it clears the last one shown", () => {
    const shown = declutter(
      [
        { id: "a", left: 0, label: "1" },
        { id: "b", left: 6, label: "2" }, // on top of 1
        { id: "c", left: 30, label: "3" },
        { id: "d", left: 34, label: "10" },
        { id: "e", left: 60, label: "E1" },
      ],
      { charWidth: 7, gap: 6 },
    );
    expect([...shown]).toEqual(["a", "c", "e"]);
    expect([...declutter([], { charWidth: 7, gap: 6 })]).toEqual([]);
  });

  it("a centred label (an extended mark's) is measured from its middle", () => {
    const shown = declutter(
      [
        { id: "x1", left: 10, label: "E1", centred: true },
        { id: "x2", left: 24, label: "E2", centred: true },
        { id: "x3", left: 60, label: "E3", centred: true },
      ],
      { charWidth: 7, gap: 6 },
    );
    expect([...shown]).toEqual(["x1", "x3"]);
  });
});

describe("stepping through the lines", () => {
  const ids = ["x001", "c001", "c002", "c005"];
  it("moves one line at a time, stops at the ends, and jumps to either end", () => {
    expect(neighbour(ids, "c001", 1)).toBe("c002");
    expect(neighbour(ids, "c001", -1)).toBe("x001");
    expect(neighbour(ids, "x001", -1)).toBe("x001");
    expect(neighbour(ids, "c005", 1)).toBe("c005");
    expect(neighbour(ids, "c002", "first")).toBe("x001");
    expect(neighbour(ids, "c002", "last")).toBe("c005");
    expect(neighbour(ids, null, 1)).toBe("x001");
    expect(neighbour(ids, "gone", -1)).toBe("x001");
    expect(neighbour([], "c001", 1)).toBeNull();
  });
});
