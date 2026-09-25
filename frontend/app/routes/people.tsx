import { Link, useSearchParams } from "react-router";

import { api, orThrow } from "~/api/client";
import { filterByName, yearRanges, yearSpan } from "~/lib/people";

import type { Route } from "./+types/people";

export function meta() {
  return [{ title: "Henkilöt – Pöytäkirjat" }];
}

export async function clientLoader() {
  return { people: orThrow(await api.GET("/api/people")) };
}

export default function People({ loaderData }: Route.ComponentProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  const filter = searchParams.get("nimi") ?? "";
  const shown = filterByName(loaderData.people, filter);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">Henkilöt</h1>
      <label className="block max-w-md">
        <span className="sr-only">Suodata nimellä</span>
        <input
          type="search"
          value={filter}
          onChange={(e) => setSearchParams(e.target.value ? { nimi: e.target.value } : {}, { replace: true })}
          placeholder="Suodata nimellä"
          className="w-full rounded-lg border border-stone-300 bg-white px-4 py-2 shadow-sm outline-none focus:border-stone-500 focus:ring-2 focus:ring-stone-200 dark:border-stone-700 dark:bg-stone-900 dark:focus:ring-stone-700"
        />
      </label>
      <p className="text-sm text-stone-600 dark:text-stone-400">
        {shown.length} / {loaderData.people.length} henkilöä. Sama henkilö voi näkyä kahdesti, jos pöytäkirjat
        kirjoittavat nimen eri tavoin (esim. pelkkä etunimi); ne yhdistetään komennolla{" "}
        <code>mi people merge</code>.
      </p>
      <div className="overflow-x-auto rounded-xl border border-stone-200 bg-white dark:border-stone-800 dark:bg-stone-900">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-stone-200 text-stone-500 dark:border-stone-800">
            <tr>
              <th scope="col" className="px-4 py-2 font-medium">
                Nimi
              </th>
              <th scope="col" className="px-4 py-2 text-right font-medium">
                Kokouksia
              </th>
              <th scope="col" className="px-4 py-2 font-medium">
                Vuodet
              </th>
              <th scope="col" className="px-4 py-2 font-medium">
                Hallituksessa
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-stone-200 dark:divide-stone-800">
            {shown.map((p) => (
              <tr key={p.id}>
                <td className="px-4 py-2">
                  <Link to={`/henkilot/${p.id}`} className="font-medium hover:underline">
                    {p.name}
                  </Link>
                </td>
                <td className="px-4 py-2 text-right tabular-nums">{p.meetings}</td>
                <td className="px-4 py-2 tabular-nums text-stone-600 dark:text-stone-400">
                  {yearSpan(p.first_year, p.last_year)}
                </td>
                <td className="px-4 py-2 tabular-nums text-stone-600 dark:text-stone-400">
                  {yearRanges(p.board_years) || "–"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
