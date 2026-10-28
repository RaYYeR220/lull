import { describe, expect, it } from "vitest";
import { wcagTone } from "../lib/wcag";

describe("a WCAG status's tone", () => {
  it("is good only when met and reviewed by a person; automated checks alone are neutral", () => {
    expect(wcagTone("met")).toBe("good");
    expect(wcagTone("met (automated checks)")).toBe("none");
    expect(wcagTone("met (no visual information beyond the soundtrack)")).toBe("none");
    expect(wcagTone("needs review")).toBe("warn");
    expect(wcagTone("not claimed")).toBe("none");
  });
});
