import { describe, expect, it } from "vitest";
import { cueText, parseTimestamp, parseVtt } from "../src/vtt";

// the engine's own output for the ed90 run (descriptions.vtt and extended.vtt), byte for byte
const ED90_DESCRIPTIONS = `WEBVTT
Kind: descriptions
Language: en

c001
00:00:09.000 --> 00:00:13.551
The bald person and a second person walk through a doorway into a bright light.

c003
00:00:23.710 --> 00:00:28.447
The two people climb onto a higher metal beam and continue walking along it.

c005
00:00:42.689 --> 00:00:46.033
The bald person runs across a beam; the other follows.

c008
00:00:57.316 --> 00:01:01.774
The two people cross a curved metal pipe high in the wire-filled space.

c012
00:01:18.100 --> 00:01:22.372
The two people fall through the dense wires and disappear into the darkness below.
`;

const ED90_EXTENDED = `WEBVTT
Kind: descriptions
Language: en

NOTE extended pause=5.573

x001
00:00:00.000 --> 00:00:05.573
A bald person in a green coat and red shirt stands in a room filled with wires and equipment.

NOTE extended pause=5.387

x002
00:00:04.708 --> 00:00:10.095
The scene changes to a high narrow walkway between tall walls covered in hanging wires.
`;

describe("parseTimestamp", () => {
  it("reads hours, minutes and seconds", () => {
    expect(parseTimestamp("00:00:09.000")).toBe(9);
    expect(parseTimestamp("01:02:03.250")).toBeCloseTo(3723.25, 6);
    expect(parseTimestamp("00:01:22.372")).toBeCloseTo(82.372, 6);
  });
  it("takes minutes without hours, a comma, short fractions and bare seconds", () => {
    expect(parseTimestamp("01:05.500")).toBeCloseTo(65.5, 6);
    expect(parseTimestamp("00:00:01,250")).toBeCloseTo(1.25, 6);
    expect(parseTimestamp("00:00:01.5")).toBeCloseTo(1.5, 6);
    expect(parseTimestamp("12.75")).toBeCloseTo(12.75, 6);
    expect(parseTimestamp("00:00:07")).toBe(7);
  });
  it("refuses what isn't a time", () => {
    for (const bad of ["", "abc", "00:00:61.000", "1:2:3:4", "-00:00:01.000"]) expect(parseTimestamp(bad)).toBeNaN();
  });
});

describe("cueText", () => {
  it("strips tags and decodes entities to one line", () => {
    expect(cueText("<v Narrator>A <i>bald</i> man</v>\n&amp; a dog &lt;3&gt;")).toBe("A bald man & a dog <3>");
    expect(cueText("<00:00:01.000>Timed <c.loud>words</c>")).toBe("Timed words");
    expect(cueText("caf&#233; &#x2014; done&nbsp;now&lrm;")).toBe("café — done now");
    expect(cueText("an &unknown; entity")).toBe("an &unknown; entity");
  });
});

describe("parseVtt", () => {
  it("reads the engine's descriptions file", () => {
    const t = parseVtt(ED90_DESCRIPTIONS);
    expect(t.kind).toBe("descriptions");
    expect(t.language).toBe("en");
    expect(t.cues.map((c) => c.id)).toEqual(["c001", "c003", "c005", "c008", "c012"]);
    expect(t.cues[0]).toEqual({
      id: "c001",
      start: 9,
      end: 13.551,
      text: "The bald person and a second person walk through a doorway into a bright light.",
    });
    expect(t.cues[4]!.start).toBeCloseTo(78.1, 6);
    expect(t.cues.every((c) => c.pause === undefined)).toBe(true);
  });

  it("reads the extended file with its pause lines", () => {
    const t = parseVtt(ED90_EXTENDED);
    expect(t.cues.map((c) => [c.id, c.start, c.pause])).toEqual([
      ["x001", 0, 5.573],
      ["x002", 4.708, 5.387],
    ]);
  });

  it("gives a NOTE pause only to the cue right after it", () => {
    const t = parseVtt("WEBVTT\n\nNOTE extended pause=2.5\n\na\n00:00:01.000 --> 00:00:02.000\nOne\n\nb\n00:00:03.000 --> 00:00:04.000\nTwo\n");
    expect(t.cues.map((c) => c.pause)).toEqual([2.5, undefined]);
  });

  it("does not hand a pause past a cue it couldn't read", () => {
    const t = parseVtt("WEBVTT\n\nNOTE extended pause=2.5\n\nbroken\n00:00:xx --> 00:00:02.000\nOne\n\nb\n00:00:03.000 --> 00:00:04.000\nTwo\n");
    expect(t.cues).toHaveLength(1);
    expect(t.cues[0]!.pause).toBeUndefined();
  });

  it("copes with CRLF, a BOM, no header and settings after the times", () => {
    const t = parseVtt("﻿00:00:01.000 --> 00:00:02.000 align:start line:90%\r\nHello\r\n\r\n00:00:03.000 --> 00:00:04.000\r\nWorld\r\n");
    expect(t.cues.map((c) => [c.id, c.start, c.end, c.text])).toEqual([
      ["", 1, 2, "Hello"],
      ["", 3, 4, "World"],
    ]);
  });

  it("joins a multi-line payload and skips STYLE, REGION and plain NOTE blocks", () => {
    const t = parseVtt(
      "WEBVTT\n\nSTYLE\n::cue { color: red }\n\nREGION\nid:r1\n\nNOTE just a comment\nover two lines\n\nc1\n00:00:01.000 --> 00:00:02.000\nfirst line\nsecond line\n",
    );
    expect(t.cues).toEqual([{ id: "c1", start: 1, end: 2, text: "first line second line" }]);
  });

  it("splits two cues that lack the blank line between them", () => {
    const t = parseVtt("WEBVTT\n\na\n00:00:01.000 --> 00:00:02.000\nOne\nb\n00:00:03.000 --> 00:00:04.000\nTwo\n");
    expect(t.cues.map((c) => [c.id, c.text])).toEqual([
      ["a", "One"],
      ["b", "Two"],
    ]);
  });

  it("skips garbage and empty cues, clamps a backwards end, sorts by start", () => {
    const t = parseVtt(
      "WEBVTT\n\nthis is not a cue\n\nz\n00:00:05.000 --> 00:00:04.000\nBackwards\n\ny\n00:00:02.000 --> 00:00:03.000\n<i></i>\n\nx\n00:00:01.000 --> 00:00:01.500\nFirst\n",
    );
    expect(t.cues.map((c) => [c.id, c.start, c.end])).toEqual([
      ["x", 1, 1.5],
      ["z", 5, 5],
    ]);
  });

  it("never throws", () => {
    for (const input of ["", "WEBVTT", "\n\n\n", "-->", "WEBVTT\n\n-->\n-->\n", null as unknown as string, "x".repeat(10_000)]) {
      expect(() => parseVtt(input)).not.toThrow();
    }
    expect(parseVtt("").cues).toEqual([]);
  });
});
