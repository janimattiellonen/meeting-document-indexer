import { Link } from "react-router";

import { api, orThrow } from "~/api/client";
import { formatDate, meetingTypeLabel } from "~/lib/format";
import { attendance, formatRoles } from "~/lib/people";

import type { Route } from "./+types/person";

export function meta({ loaderData }: Route.MetaArgs) {
  return [{ title: loaderData ? `${loaderData.person.name} – Pöytäkirjat` : "Henkilö – Pöytäkirjat" }];
}

export async function clientLoader({ params }: Route.ClientLoaderArgs) {
  const personId = Number(params.personId);
  if (!Number.isInteger(personId)) throw new Response("Not found", { status: 404 });
  const response = await api.GET("/api/people/{person_id}", { params: { path: { person_id: personId } } });
  return { person: orThrow(response) };
}

const STATUS = { present: "läsnä", absent: "poissa" } as const;

export default function Person({ loaderData }: Route.ComponentProps) {
  const { person } = loaderData;
  const otherSpellings = person.spellings.filter((s) => s !== person.name);

  return (
    <div className="space-y-8">
      <Link to="/henkilot" className="text-sm text-stone-500 hover:underline">
        ← Henkilöt
      </Link>
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">{person.name}</h1>
        {otherSpellings.length > 0 && (
          <p className="mt-1 text-sm text-stone-600 dark:text-stone-400">
            Myös kirjoitettu: {otherSpellings.join(", ")}
          </p>
        )}
      </header>

      <section>
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-stone-500">Hallituksessa</h2>
        {person.board_terms.length === 0 ? (
          <p className="text-sm text-stone-600 dark:text-stone-400">
            Ei hallituksen kokousten läsnäolijoissa.
          </p>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-stone-200 bg-white dark:border-stone-800 dark:bg-stone-900">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-stone-200 text-stone-500 dark:border-stone-800">
                <tr>
                  <th scope="col" className="px-4 py-2 font-medium">
                    Vuosi
                  </th>
                  <th scope="col" className="px-4 py-2 font-medium">
                    Tehtävät
                  </th>
                  <th scope="col" className="px-4 py-2 text-right font-medium">
                    Läsnä
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-stone-200 dark:divide-stone-800">
                {person.board_terms.map((t) => (
                  <tr key={t.year}>
                    <td className="px-4 py-2 tabular-nums">
                      <Link to={`/hallitus/${t.year}`} className="hover:underline">
                        {t.year}
                      </Link>
                    </td>
                    <td className="px-4 py-2 text-stone-600 dark:text-stone-400">
                      {formatRoles(t.roles, t.meetings) || "–"}
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums">{attendance(t.present, t.meetings)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-stone-500">
          Kokoukset ({person.meetings.length})
        </h2>
        <ul className="divide-y divide-stone-200 overflow-hidden rounded-xl border border-stone-200 bg-white dark:divide-stone-800 dark:border-stone-800 dark:bg-stone-900">
          {person.meetings.map((m) => (
            <li key={m.meeting_id}>
              <Link
                to={`/kokoukset/${m.meeting_id}`}
                className="flex flex-wrap items-baseline justify-between gap-2 px-5 py-2.5 hover:bg-stone-50 dark:hover:bg-stone-800/50"
              >
                <span>
                  <span className="font-medium">{m.title}</span>
                  <span className="ml-3 text-sm text-stone-500">{meetingTypeLabel(m.meeting_type)}</span>
                </span>
                <span className="text-sm text-stone-500">
                  {formatDate(m.meeting_date)} · {STATUS[m.status as keyof typeof STATUS] ?? m.status}
                  {m.role && ` · ${m.role}`}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
