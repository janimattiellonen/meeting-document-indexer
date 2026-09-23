import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Highlight, highlightParts, plainText } from "./Highlight";

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

describe("Highlight", () => {
  it("renders matches in <mark> and never interprets document text as HTML", () => {
    const { container } = render(<Highlight text={`<img src=x onerror=alert(1)> ${S}osuma${E}`} />);

    expect(screen.getByText("osuma").tagName).toBe("MARK");
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toBe("<img src=x onerror=alert(1)> osuma");
  });
});

describe("plainText", () => {
  it("drops the markers", () => {
    expect(plainText(`Seuran ${S}verkkosivut${E}`)).toBe("Seuran verkkosivut");
  });
});
