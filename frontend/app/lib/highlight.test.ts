import { describe, expect, it } from "vitest";

import { highlightParts, plainText } from "./highlight";

const S = "\u0002";
const E = "\u0003";

describe("highlightParts", () => {
  it("splits marked matches from the surrounding text", () => {
    expect(highlightParts(`Hankitaan ${S}kannettava${E} tietokone`)).toEqual([
      { text: "Hankitaan ", match: false },
      { text: "kannettava", match: true },
      { text: " tietokone", match: false },
    ]);
  });

  it("handles several matches, and matches at the start and end", () => {
    expect(highlightParts(`${S}a${E} b ${S}c${E}`)).toEqual([
      { text: "a", match: true },
      { text: " b ", match: false },
      { text: "c", match: true },
    ]);
  });

  it("treats an unterminated marker as a match to the end", () => {
    expect(highlightParts(`x ${S}y`)).toEqual([
      { text: "x ", match: false },
      { text: "y", match: true },
    ]);
  });

  it("returns plain text unchanged", () => {
    expect(highlightParts("ei osumia")).toEqual([{ text: "ei osumia", match: false }]);
  });
});

describe("plainText", () => {
  it("drops the markers", () => {
    expect(plainText(`Seuran ${S}verkkosivut${E}`)).toBe("Seuran verkkosivut");
  });
});
