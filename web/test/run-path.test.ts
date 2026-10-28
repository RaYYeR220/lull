import { describe, expect, it } from "vitest";
import { runIdFromPath } from "../lib/run-path";

describe("a live run's id, from its address", () => {
  it("is the segment after /run/, on the run's page and its report", () => {
    expect(runIdFromPath("/run/live-20261027-214500-a1b2c3")).toBe("live-20261027-214500-a1b2c3");
    expect(runIdFromPath("/run/live-20261027-214500-a1b2c3/report")).toBe("live-20261027-214500-a1b2c3");
    expect(runIdFromPath("/run/a%2Db/")).toBe("a-b");
  });

  it("is none on the shells' own addresses or anything else", () => {
    expect(runIdFromPath("/run")).toBeNull();
    expect(runIdFromPath("/run/report")).toBeNull();
    expect(runIdFromPath("/run/a/b")).toBeNull();
    expect(runIdFromPath("/run/%E0%A4%A")).toBeNull();
  });
});
