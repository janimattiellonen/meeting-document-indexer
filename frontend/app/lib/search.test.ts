import { describe, expect, it } from "vitest";

import { MAX_QUERY_LENGTH, parseSearch } from "./search";

const parse = (query: string) => parseSearch(new URLSearchParams(query));

describe("parseSearch", () => {
  it("reads the search from the URL", () => {
    expect(parse("q=+verkkosivut+&jarjestys=oldest&alkaen=2019&asti=2021")).toEqual({
      q: "verkkosivut",
      sort: "oldest",
      yearFrom: 2019,
      yearTo: 2021,
    });
  });

  it.each(["toString", "constructor", "__proto__", "unknown"])(
    "falls back to relevance for the sort %s",
    (sort) => {
      expect(parse(`q=x&jarjestys=${sort}`).sort).toBe("relevance");
    },
  );

  it("shortens a query to what the API accepts", () => {
    expect(parse(`q=${"a".repeat(300)}`).q).toHaveLength(MAX_QUERY_LENGTH);
  });

  it("ignores years the API would reject", () => {
    expect(parse("alkaen=1899&asti=3000")).toMatchObject({ yearFrom: undefined, yearTo: undefined });
    expect(parse("alkaen=1900&asti=2999")).toMatchObject({ yearFrom: 1900, yearTo: 2999 });
  });
});
