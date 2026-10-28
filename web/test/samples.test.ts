import { describe, expect, it } from "vitest";
import { sampleItems, sampleTooLong } from "../lib/samples";

const RECORDED = [{ id: "ed90", title: "Elephants Dream", credit: "Elephants Dream (2006), Blender Foundation / Netherlands Media Art Institute, CC BY 2.5", duration: 90 }];

describe("the try page's sample cards", () => {
  it("come from the API's own metadata: title, length, credit with licence", () => {
    const [csc, ed] = sampleItems(
      [
        { id: "csc_intro", title: "Climate Science Champions, introduction", duration: 44.9, credit: "USGS", licence: "public domain" },
        { id: "ed45", title: "Elephants Dream, an excerpt", duration: 45, credit: "Elephants Dream (2006), Blender Foundation / Orange Open Movie Team", licence: "CC BY 2.5" },
      ],
      RECORDED,
    );
    expect(csc).toEqual({ id: "csc_intro", title: "Climate Science Champions, introduction", duration: 44.9, credit: "USGS, public domain", recorded: false });
    expect(ed!.credit).toBe("Elephants Dream (2006), Blender Foundation / Orange Open Movie Team, CC BY 2.5");
    expect(ed!.recorded).toBe(false); // ed45 is not the recorded run ed90
  });

  it("a sample that is also a recorded run links to it; with no metadata, the recorded run's facts or the id", () => {
    const [ed90, bare] = sampleItems(
      [
        { id: "ed90", title: null, duration: null, credit: null, licence: null },
        { id: "tos45", title: null, duration: null, credit: null, licence: null },
      ],
      RECORDED,
    );
    expect(ed90).toEqual({ id: "ed90", title: "Elephants Dream", duration: 90, credit: RECORDED[0]!.credit, recorded: true });
    expect(bare).toEqual({ id: "tos45", title: "tos45", duration: null, credit: null, recorded: false });
  });

  it("a sample longer than the API takes (45 s) is stopped here, saying why", () => {
    expect(sampleTooLong({ duration: 44.9 })).toBeNull();
    expect(sampleTooLong({ duration: null })).toBeNull();
    expect(sampleTooLong({ duration: 90 })).toBe("This sample is 1:30 long; the live service takes samples up to 0:45.");
  });
});
