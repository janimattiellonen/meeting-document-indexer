import { describe, expect, it } from "vitest";

import type { MeetingSummary } from "~/api/client";

import { byYear, meetingUrl, parseMeetingParams } from "./meetings";

describe("byYear", () => {
  it("groups meetings by year, keeping the API's order", () => {
    const meetings = [
      { id: 3, meeting_date: "2021-05-01" },
      { id: 2, meeting_date: "2021-01-10" },
      { id: 1, meeting_date: "2019-04-02" },
      { id: 0, meeting_date: null },
    ] as MeetingSummary[];

    expect(byYear(meetings).map(([year, group]) => [year, group.map((m) => m.id)])).toEqual([
      ["2021", [3, 2]],
      ["2019", [1]],
      ["Ei päivämäärää", [0]],
    ]);
  });
});

describe("meetingUrl", () => {
  it("links to the meeting, with a page and a topic when given", () => {
    expect(meetingUrl({ meetingId: 7 })).toBe("/kokoukset/7");
    expect(meetingUrl({ meetingId: 7, page: 3 })).toBe("/kokoukset/7?sivu=3");
    expect(meetingUrl({ meetingId: 7, page: 3, topicId: 12 })).toBe("/kokoukset/7?sivu=3&kohta=12");
    expect(meetingUrl({ meetingId: 7, page: null, topicId: 12 })).toBe("/kokoukset/7?kohta=12");
  });
});

describe("parseMeetingParams", () => {
  const parse = (query: string) => parseMeetingParams(new URLSearchParams(query));

  it("reads what meetingUrl writes", () => {
    expect(parse("sivu=3&kohta=12")).toEqual({ page: 3, topicId: 12 });
    expect(parse("")).toEqual({ page: null, topicId: null });
  });

  it.each(["0", "-2", "1.5", "abc"])("ignores the value %s", (value) => {
    expect(parse(`sivu=${value}&kohta=${value}`)).toEqual({ page: null, topicId: null });
  });
});
