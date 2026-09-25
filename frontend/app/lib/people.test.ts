import { describe, expect, it } from "vitest";

import { attendance, filterByName, formatRoles, yearRanges, yearSpan } from "./people";

describe("yearRanges", () => {
  it("joins consecutive years into ranges", () => {
    expect(yearRanges([2023, 2019, 2020, 2021])).toBe("2019–2021, 2023");
    expect(yearRanges([2020])).toBe("2020");
    expect(yearRanges([])).toBe("");
  });
});

describe("formatRoles", () => {
  it("shows a count only for roles held in some of the meetings", () => {
    const roles = [
      { name: "puheenjohtaja", meetings: 12 },
      { name: "sihteeri", meetings: 2 },
    ];
    expect(formatRoles(roles, 12)).toBe("puheenjohtaja, sihteeri (2/12)");
    expect(formatRoles([], 12)).toBe("");
  });
});

describe("attendance", () => {
  it("is present out of all meetings", () => {
    expect(attendance(8, 10)).toBe("8/10");
  });
});

describe("filterByName", () => {
  const people = [{ name: "Maija Meikäläinen" }, { name: "Teppo Testaaja" }];

  it("matches every word in any order, ignoring case", () => {
    expect(filterByName(people, "meikäl MAIJA")).toEqual([{ name: "Maija Meikäläinen" }]);
    expect(filterByName(people, "  ")).toEqual(people);
    expect(filterByName(people, "liisa")).toEqual([]);
  });

  it("matches any spelling of the name, such as a nickname", () => {
    const spelled = [
      { name: "Antti Esimerkki", spellings: ["Antti (Andy) Esimerkki", "Esimerkki, Antti"] },
      { name: "Teppo Testaaja", spellings: ["Teppo Testaaja"] },
    ];
    expect(filterByName(spelled, "andy").map((p) => p.name)).toEqual(["Antti Esimerkki"]);
    // Every word has to be in the same spelling.
    expect(filterByName(spelled, "andy teppo")).toEqual([]);
  });
});

describe("yearSpan", () => {
  it("is first to last year", () => {
    expect(yearSpan(2014, 2018)).toBe("2014–2018");
    expect(yearSpan(2014, 2014)).toBe("2014");
    expect(yearSpan(null, null)).toBe("–");
  });
});
