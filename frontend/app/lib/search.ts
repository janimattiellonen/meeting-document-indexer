import type { operations } from "~/api/schema";

// From the generated API schema: a sort added to the backend fails typecheck until it has a label here.
export type Sort = NonNullable<operations["search_meetings_api_search_get"]["parameters"]["query"]["sort"]>;

export const SORTS: Record<Sort, string> = {
  relevance: "Osuvin ensin",
  oldest: "Vanhin ensin",
  newest: "Uusin ensin",
};
// The API rejects longer queries (Query(max_length=200) in backend api/__init__.py).
export const MAX_QUERY_LENGTH = 200;

/** The search in the URL (?q=…&alkaen=…&asti=…&jarjestys=…), limited to what the API accepts. */
export function parseSearch(params: URLSearchParams): {
  q: string;
  sort: Sort;
  yearFrom: number | undefined;
  yearTo: number | undefined;
} {
  const q = (params.get("q") ?? "").trim().slice(0, MAX_QUERY_LENGTH).trimEnd();
  const sort = params.get("jarjestys");
  return {
    q,
    sort: isSort(sort) ? sort : "relevance",
    yearFrom: parseYear(params.get("alkaen")),
    yearTo: parseYear(params.get("asti")),
  };
}

function parseYear(value: string | null): number | undefined {
  const year = Number(value);
  return value && Number.isInteger(year) && year >= 1900 && year <= 2999 ? year : undefined;
}

function isSort(value: string | null): value is Sort {
  // hasOwn, not `in`: "toString" is in every object.
  return value !== null && Object.hasOwn(SORTS, value);
}
