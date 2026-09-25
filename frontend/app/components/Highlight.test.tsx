import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Highlight } from "./Highlight";

const S = "\u0002";
const E = "\u0003";

describe("Highlight", () => {
  it("renders matches in <mark> and never interprets document text as HTML", () => {
    const { container } = render(<Highlight text={`<img src=x onerror=alert(1)> ${S}osuma${E}`} />);

    expect(screen.getByText("osuma").tagName).toBe("MARK");
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toBe("<img src=x onerror=alert(1)> osuma");
  });
});
