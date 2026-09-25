import { Link, NavLink } from "react-router";

import { api, type Board, type BoardMember, type BoardYear, orThrow } from "~/api/client";
import { formatDate } from "~/lib/format";
import { attendance, formatRoles } from "~/lib/people";

import type { Route } from "./+types/board";

export function meta({ params }: Route.MetaArgs) {
  return [{ title: `Hallitus ${params.year} – Pöytäkirjat` }];
}

export async function clientLoader({ params }: Route.ClientLoaderArgs) {
  const year = Number(params.year);
  if (!Number.isInteger(year) || year < 1900 || year > 2999) throw new Response("Not found", { status: 404 });
  const [board, years] = await Promise.all([
    api.GET("/api/boards/{year}", { params: { path: { year } } }),
    api.GET("/api/boards"),
  ]);
  return { board: orThrow(board), years: orThrow(years) };
}

export default function BoardPage({ loaderData }: Route.ComponentProps) {
  const { board, years } = loaderData;
  const total = board.meetings.length;
  return (
    <div className="space-y-6">
      <YearNav years={years} />
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Hallitus {board.year}</h1>
        <p className="mt-1 text-sm text-stone-600 dark:text-stone-400">
          Päätelty hallituksen kokousten läsnäoloista ({total} kokousta), ei vaalien tuloksista. Tarkista
          tärkeät tiedot pöytäkirjoista.
        </p>
      </header>
      <MemberTable members={board.members} meetings={total} />
      {board.others.length > 0 && (
        <section>
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-stone-500">
            Muut osallistujat
          </h2>
          <p className="mb-2 text-sm text-stone-600 dark:text-stone-400">
            Pöytäkirja merkitsee heidät hallituksen ulkopuolisiksi.
          </p>
          <MemberTable members={board.others} meetings={total} />
        </section>
      )}
      <MeetingList board={board} />
    </div>
  );
}

function YearNav({ years }: { years: BoardYear[] }) {
  const link = ({ isActive }: { isActive: boolean }) =>
    `rounded-md px-2 py-1 text-sm tabular-nums ${
      isActive
        ? "bg-stone-900 text-white dark:bg-stone-100 dark:text-stone-900"
        : "text-stone-600 hover:bg-stone-200 dark:text-stone-300 dark:hover:bg-stone-800"
    }`;
  return (
    <nav aria-label="Vuodet" className="flex flex-wrap gap-1">
      {years.map((y) => (
        <NavLink key={y.year} to={`/hallitus/${y.year}`} className={link}>
          {y.year}
        </NavLink>
      ))}
    </nav>
  );
}

function MemberTable({ members, meetings }: { members: BoardMember[]; meetings: number }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-stone-200 bg-white dark:border-stone-800 dark:bg-stone-900">
      <table className="w-full text-left text-sm">
        <thead className="border-b border-stone-200 text-stone-500 dark:border-stone-800">
          <tr>
            <th scope="col" className="px-4 py-2 font-medium">
              Nimi
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
          {members.map((m) => (
            <tr key={m.person_id}>
              <td className="px-4 py-2">
                <Link to={`/henkilot/${m.person_id}`} className="font-medium hover:underline">
                  {m.name}
                </Link>
              </td>
              <td className="px-4 py-2 text-stone-600 dark:text-stone-400">
                {formatRoles(m.roles, meetings) || "–"}
              </td>
              <td className="px-4 py-2 text-right tabular-nums">{attendance(m.present, meetings)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MeetingList({ board }: { board: Board }) {
  return (
    <section>
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-stone-500">
        Hallituksen kokoukset
      </h2>
      <ul className="space-y-1 text-sm">
        {board.meetings.map((m) => (
          <li key={m.id}>
            <Link to={`/kokoukset/${m.id}`} className="hover:underline">
              {formatDate(m.meeting_date)} · {m.title}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
