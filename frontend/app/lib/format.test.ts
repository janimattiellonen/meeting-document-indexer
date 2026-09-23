import { describe, expect, it } from "vitest";

import { formatDate, formatTime, meetingTypeLabel, yearOf } from "./format";

describe("format", () => {
  it("formats dates the Finnish way without a time zone shift", () => {
    expect(formatDate("2026-05-12")).toBe("12.5.2026");
    expect(formatDate("2025-12-08")).toBe("8.12.2025");
    expect(formatDate(null)).toBe("päivämäärä puuttuu");
  });

  it("formats times with a dot", () => {
    expect(formatTime("18:05:00")).toBe("18.05");
    expect(formatTime(null)).toBeNull();
  });

  it("names meeting types in Finnish and passes unknown ones through", () => {
    expect(meetingTypeLabel("board")).toBe("Hallituksen kokous");
    expect(meetingTypeLabel("autumn_general")).toBe("Syyskokous");
    expect(meetingTypeLabel("something")).toBe("something");
  });

  it("takes the year of a date", () => {
    expect(yearOf("2019-04-02")).toBe("2019");
    expect(yearOf(null)).toBe("Ei päivämäärää");
  });
});
