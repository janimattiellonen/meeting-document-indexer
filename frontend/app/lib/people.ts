import type { Role } from "~/api/client";

/** [2019, 2020, 2021, 2023] -> "2019–2021, 2023" */
export function yearRanges(years: number[]): string {
  const sorted = [...new Set(years)].sort((a, b) => a - b);
  const ranges: string[] = [];
  for (let i = 0; i < sorted.length; i++) {
    const start = sorted[i];
    while (i + 1 < sorted.length && sorted[i + 1] === sorted[i] + 1) i++;
    ranges.push(start === sorted[i] ? `${start}` : `${start}–${sorted[i]}`);
  }
  return ranges.join(", ");
}

/** First to last year: "2014–2018", or "2014" when they're the same. */
export function yearSpan(first: number | null | undefined, last: number | null | undefined): string {
  if (!first) return "–";
  return !last || last === first ? `${first}` : `${first}–${last}`;
}

/**
 * "puheenjohtaja, sihteeri (2/12)": a role held in every listed meeting needs no count, one held in only
 * some of them (a meeting secretary who rotates) shows in how many.
 */
export function formatRoles(roles: Role[], meetings: number): string {
  return roles
    .map((r) => (r.meetings >= meetings ? r.name : `${r.name} (${r.meetings}/${meetings})`))
    .join(", ");
}

/** "8/10": in how many of the year's board meetings the person was present. */
export function attendance(present: number, meetings: number): string {
  return `${present}/${meetings}`;
}

/** People whose name, or one of its spellings in the minutes, contains every word of the filter, ignoring case. */
export function filterByName<T extends { name: string; spellings?: string[] }>(
  people: T[],
  filter: string,
): T[] {
  const words = filter.toLocaleLowerCase("fi").split(/\s+/).filter(Boolean);
  return people.filter((p) =>
    [p.name, ...(p.spellings ?? [])].some((spelling) => {
      const name = spelling.toLocaleLowerCase("fi");
      return words.every((w) => name.includes(w));
    }),
  );
}
