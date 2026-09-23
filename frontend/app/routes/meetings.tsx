import { Link } from "react-router";

import { api, type MeetingSummary, orThrow } from "~/api/client";
import { formatDate, meetingTypeLabel, yearOf } from "~/lib/format";

import type { Route } from "./+types/meetings";

export function meta() {
  return [{ title: "Kokoukset – Pöytäkirjat" }];
}

export async function clientLoader() {
  return { meetings: orThrow(await api.GET("/api/meetings")) };
}

/** Meetings grouped by year, newest year first (the API returns them newest first). */
export function byYear(meetings: MeetingSummary[]): [string, MeetingSummary[]][] {
  const groups = new Map<string, MeetingSummary[]>();
  for (const meeting of meetings) {
    const year = yearOf(meeting.meeting_date);
    groups.set(year, [...(groups.get(year) ?? []), meeting]);
  }
  return [...groups];
}

export default function Meetings({ loaderData }: Route.ComponentProps) {
  const { meetings } = loaderData;
  if (meetings.length === 0) {
    return (
      <p className="text-stone-600 dark:text-stone-400">
        Kokouksia ei ole vielä indeksoitu. Aja <code>uv run mi index</code> backend-kansiossa.
      </p>
    );
  }
  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-semibold tracking-tight">Kokoukset</h1>
      {byYear(meetings).map(([year, group]) => (
        <section key={year}>
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-stone-500">{year}</h2>
          <ul className="divide-y divide-stone-200 overflow-hidden rounded-xl border border-stone-200 bg-white dark:divide-stone-800 dark:border-stone-800 dark:bg-stone-900">
            {group.map((meeting) => (
              <li key={meeting.id}>
                <Link
                  to={`/kokoukset/${meeting.id}`}
                  className="flex flex-wrap items-baseline justify-between gap-2 px-5 py-3 hover:bg-stone-50 dark:hover:bg-stone-800/50"
                >
                  <span>
                    <span className="font-medium">{meeting.title}</span>
                    <span className="ml-3 text-sm text-stone-500">
                      {meetingTypeLabel(meeting.meeting_type)}
                      {meeting.location && ` · ${meeting.location}`}
                    </span>
                  </span>
                  <span className="text-sm text-stone-500">
                    {formatDate(meeting.meeting_date)} · {meeting.topic_count} asiakohtaa, {meeting.decision_count}{" "}
                    päätöstä
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
