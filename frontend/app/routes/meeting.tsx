import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { api, documentUrl, type MeetingView, orThrow, type TopicView } from "~/api/client";

type Attendee = MeetingView["attendees"][number];
import { formatDate, formatTime, meetingTypeLabel } from "~/lib/format";

import type { Route } from "./+types/meeting";

export function meta({ loaderData }: Route.MetaArgs) {
  return [{ title: loaderData ? `${loaderData.meeting.title} – Pöytäkirjat` : "Kokous – Pöytäkirjat" }];
}

export async function clientLoader({ params }: Route.ClientLoaderArgs) {
  const meetingId = Number(params.meetingId);
  if (!Number.isInteger(meetingId)) throw new Response("Not found", { status: 404 });
  const response = await api.GET("/api/meetings/{meeting_id}", {
    params: { path: { meeting_id: meetingId } },
  });
  return { meeting: orThrow(response) };
}

export default function Meeting({ loaderData }: Route.ComponentProps) {
  const [searchParams] = useSearchParams();
  // key: start fresh (page, selected item) when the URL changes, e.g. another search hit in the same meeting.
  return (
    <MeetingPage key={searchParams.toString()} meeting={loaderData.meeting} searchParams={searchParams} />
  );
}

function MeetingPage({ meeting, searchParams }: { meeting: MeetingView; searchParams: URLSearchParams }) {
  const selectedTopic = Number(searchParams.get("kohta")) || null;
  const [page, setPage] = useState<number | null>(Number(searchParams.get("sivu")) || null);

  return (
    <div className="space-y-6">
      <Link to=".." relative="path" className="text-sm text-stone-500 hover:underline">
        ← Kokoukset
      </Link>
      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <Details meeting={meeting} selectedTopic={selectedTopic} onShowPage={setPage} />
        <DocumentPanel meeting={meeting} page={page} />
      </div>
    </div>
  );
}

function Details({
  meeting,
  selectedTopic,
  onShowPage,
}: {
  meeting: MeetingView;
  selectedTopic: number | null;
  onShowPage: (page: number) => void;
}) {
  const present = meeting.attendees.filter((a) => a.status === "present");
  const absent = meeting.attendees.filter((a) => a.status === "absent");
  const start = formatTime(meeting.start_time);
  const end = formatTime(meeting.end_time);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">{meeting.title}</h1>
        <p className="mt-1 text-stone-600 dark:text-stone-400">
          {meetingTypeLabel(meeting.meeting_type)} · {formatDate(meeting.meeting_date)}
          {start && ` klo ${start}${end ? `–${end}` : ""}`}
          {meeting.location && ` · ${meeting.location}`}
        </p>
      </header>

      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        <dt className="text-stone-500">Läsnä</dt>
        <dd>
          <Attendees attendees={present} withRoles />
        </dd>
        <dt className="text-stone-500">Poissa</dt>
        <dd>
          <Attendees attendees={absent} />
        </dd>
      </dl>

      {meeting.summary && (
        <section>
          <h2 className="mb-1 text-sm font-semibold uppercase tracking-wide text-stone-500">Yhteenveto</h2>
          <p className="text-stone-700 dark:text-stone-300">{meeting.summary}</p>
        </section>
      )}

      <section>
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-stone-500">Asiakohdat</h2>
        <ol className="space-y-2">
          {meeting.topics.map((topic) => (
            <li key={topic.id}>
              <TopicItem topic={topic} selected={topic.id === selectedTopic} onShowPage={onShowPage} />
            </li>
          ))}
        </ol>
      </section>

      <p className="text-xs text-stone-500">
        Tiedot on poimittu pöytäkirjasta automaattisesti ja voivat sisältää virheitä. Lähde on aina
        alkuperäinen pöytäkirja.
      </p>
    </div>
  );
}

/** Names as the minutes write them, each linking to the person. */
function Attendees({ attendees, withRoles = false }: { attendees: Attendee[]; withRoles?: boolean }) {
  if (attendees.length === 0) return <>–</>;
  return (
    <>
      {attendees.map((a, i) => (
        <span key={a.person_id}>
          {i > 0 && ", "}
          <Link to={`/henkilot/${a.person_id}`} className="hover:underline" title={a.person_name}>
            {a.name}
          </Link>
          {withRoles && a.role && ` (${a.role})`}
        </span>
      ))}
    </>
  );
}

function TopicItem({
  topic,
  selected,
  onShowPage,
}: {
  topic: TopicView;
  selected: boolean;
  onShowPage: (page: number) => void;
}) {
  const ref = useRef<HTMLElement>(null);
  // The item a search result pointed to is often far down the list.
  useEffect(() => {
    if (selected) ref.current?.scrollIntoView({ block: "center" });
  }, [selected]);

  return (
    <article
      ref={ref}
      className={`rounded-lg border p-3 ${
        selected
          ? "border-amber-400 bg-amber-50 dark:border-amber-600 dark:bg-amber-950/40"
          : "border-stone-200 bg-white dark:border-stone-800 dark:bg-stone-900"
      }`}
    >
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="font-medium">
          {topic.item_number && <span className="text-stone-500">{topic.item_number}. </span>}
          {topic.title}
        </h3>
        {topic.page_no && (
          <button
            type="button"
            onClick={() => onShowPage(topic.page_no!)}
            className="shrink-0 text-xs text-stone-500 underline hover:text-stone-900 dark:hover:text-stone-200"
          >
            Näytä s. {topic.page_no}
          </button>
        )}
      </div>
      {topic.summary && <p className="mt-1 text-sm text-stone-600 dark:text-stone-400">{topic.summary}</p>}
      {topic.decisions && topic.decisions !== topic.summary && (
        <p className="mt-1 text-sm text-emerald-800 dark:text-emerald-300">
          <span className="font-medium">Päätös:</span> {topic.decisions}
        </p>
      )}
    </article>
  );
}

function DocumentPanel({ meeting, page }: { meeting: MeetingView; page: number | null }) {
  const url = documentUrl(meeting.document_id, page);
  const isPdf = meeting.file_type === "pdf";

  return (
    <aside className="space-y-2 lg:sticky lg:top-4 lg:self-start">
      <div className="flex items-baseline justify-between gap-2 text-sm">
        <span className="truncate text-stone-500" title={meeting.rel_path}>
          {meeting.rel_path}
        </span>
        <a href={url} target="_blank" rel="noreferrer" className="shrink-0 underline">
          Avaa uuteen välilehteen
        </a>
      </div>
      {isPdf ? (
        // key: remount on page change; most PDF viewers ignore a changed #page on an open document.
        <iframe
          key={url}
          src={url}
          title={`Pöytäkirja: ${meeting.title}`}
          className="h-[80vh] w-full rounded-lg border border-stone-200 bg-white dark:border-stone-800"
        />
      ) : (
        <p className="rounded-lg border border-stone-200 bg-white p-4 text-sm dark:border-stone-800 dark:bg-stone-900">
          Selain ei näytä {meeting.file_type}-tiedostoja suoraan.{" "}
          <a href={url} className="underline">
            Lataa pöytäkirja
          </a>
          .
        </p>
      )}
    </aside>
  );
}
