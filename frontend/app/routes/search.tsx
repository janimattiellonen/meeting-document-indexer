import { Form, Link, useNavigation } from "react-router";

import { api, documentUrl, type MeetingHit, orThrow } from "~/api/client";
import { Highlight } from "~/components/Highlight";
import { formatDate, meetingTypeLabel } from "~/lib/format";
import { meetingUrl } from "~/lib/meetings";
import { MAX_QUERY_LENGTH, parseSearch, SORTS } from "~/lib/search";

import type { Route } from "./+types/search";

export function meta({ loaderData }: Route.MetaArgs) {
  return [{ title: loaderData?.q ? `${loaderData.q} – Pöytäkirjat` : "Haku – Pöytäkirjat" }];
}

// Search state lives in the URL (?q=…&alkaen=…), so every search can be bookmarked and shared.
export async function clientLoader({ request }: Route.ClientLoaderArgs) {
  const { q, sort, yearFrom, yearTo } = parseSearch(new URL(request.url).searchParams);
  if (!q) {
    return { q, sort, yearFrom, yearTo, results: null };
  }

  const response = await api.GET("/api/search", {
    params: { query: { q, sort, year_from: yearFrom, year_to: yearTo } },
  });
  return { q, sort, yearFrom, yearTo, results: orThrow(response).results };
}

export default function Search(props: Route.ComponentProps) {
  const searching = useNavigation().state === "loading";

  return (
    <div className="space-y-8">
      {/* key: re-create the inputs when the URL changes (back button), so they show the current search */}
      <Form
        method="get"
        key={`${props.loaderData.q}|${props.loaderData.sort}|${props.loaderData.yearFrom}|${props.loaderData.yearTo}`}
        className="space-y-3"
      >
        <div className="flex gap-2">
          <input
            type="search"
            name="q"
            defaultValue={props.loaderData.q}
            maxLength={MAX_QUERY_LENGTH}
            placeholder="Hae pöytäkirjoista, esim. verkkosivut, t-paidat, kannettava"
            aria-label="Hakusanat"
            autoFocus
            className="w-full rounded-lg border border-stone-300 bg-white px-4 py-2.5 text-base shadow-sm outline-none focus:border-stone-500 focus:ring-2 focus:ring-stone-200 dark:border-stone-700 dark:bg-stone-900 dark:focus:ring-stone-700"
          />
          <button
            type="submit"
            className="rounded-lg bg-stone-900 px-5 py-2.5 font-medium text-white hover:bg-stone-700 dark:bg-stone-100 dark:text-stone-900 dark:hover:bg-stone-300"
          >
            Hae
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-4 text-sm text-stone-600 dark:text-stone-400">
          <label className="flex items-center gap-2">
            Vuodesta
            <input
              name="alkaen"
              type="number"
              inputMode="numeric"
              defaultValue={props.loaderData.yearFrom}
              placeholder="2006"
              className={yearInput}
            />
          </label>
          <label className="flex items-center gap-2">
            vuoteen
            <input
              name="asti"
              type="number"
              inputMode="numeric"
              defaultValue={props.loaderData.yearTo}
              placeholder="2026"
              className={yearInput}
            />
          </label>
          <label className="flex items-center gap-2">
            Järjestys
            <select
              name="jarjestys"
              defaultValue={props.loaderData.sort}
              onChange={(e) => e.currentTarget.form?.requestSubmit()}
              className="rounded-md border border-stone-300 bg-white px-2 py-1 dark:border-stone-700 dark:bg-stone-900"
            >
              {Object.entries(SORTS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
        </div>
      </Form>

      <div className={searching ? "opacity-50 transition-opacity" : undefined}>
        {props.loaderData.results === null ? (
          <Intro />
        ) : (
          <Results q={props.loaderData.q} results={props.loaderData.results} />
        )}
      </div>
    </div>
  );
}

const yearInput =
  "w-20 rounded-md border border-stone-300 bg-white px-2 py-1 dark:border-stone-700 dark:bg-stone-900";

function Intro() {
  return (
    <div className="max-w-2xl space-y-2 text-stone-600 dark:text-stone-400">
      <p>
        Haku kohdistuu kokousten asiakohtiin, päätöksiin ja pöytäkirjojen tekstiin. Taivutusmuodolla ei ole
        väliä: <em>hallitus</em> löytää myös <em>hallituksen</em>, ja <em>kisa</em> myös{" "}
        <em>seuramestaruuskisat</em>. Nimistä riittää alku.
      </p>
      <p>
        Tulokset ovat poimintoja; alkuperäinen pöytäkirja on aina lähde. Kaikki kokoukset löytyvät{" "}
        <Link to="/kokoukset" className="underline">
          kokouslistasta
        </Link>
        .
      </p>
    </div>
  );
}

type ResultsProps = {
  q: string;
  results: MeetingHit[];
};

function Results(props: ResultsProps) {
  if (props.results.length === 0) {
    return (
      <p className="text-stone-600 dark:text-stone-400">
        Ei osumia haulle <strong>{props.q}</strong>. Kokeile toista sanaa tai vähemmän hakusanoja.
      </p>
    );
  }
  return (
    <div className="space-y-4">
      <p className="text-sm text-stone-500">
        {props.results.length} {props.results.length === 1 ? "kokous" : "kokousta"}
      </p>
      <ol className="space-y-4">
        {props.results.map((hit) => (
          <li key={hit.meeting_id}>
            <ResultCard hit={hit} />
          </li>
        ))}
      </ol>
    </div>
  );
}

type ResultCardProps = {
  hit: MeetingHit;
};

function ResultCard(props: ResultCardProps) {
  const topics = props.hit.topics ?? [];
  const text = props.hit.text ?? [];

  return (
    <article className="rounded-xl border border-stone-200 bg-white p-5 shadow-sm dark:border-stone-800 dark:bg-stone-900">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold">
          <Link to={meetingUrl({ meetingId: props.hit.meeting_id })} className="hover:underline">
            {props.hit.title}
          </Link>
        </h2>
        <span className="text-sm text-stone-500">
          {formatDate(props.hit.meeting_date)} · {meetingTypeLabel(props.hit.meeting_type)} ·{" "}
          {/* The original, at the first page with a match */}
          <a
            href={documentUrl(props.hit.document_id, topics[0]?.page_no ?? text[0]?.page_no)}
            target="_blank"
            rel="noreferrer"
            className="underline hover:text-stone-800 dark:hover:text-stone-200"
          >
            Avaa pöytäkirja
          </a>
        </span>
      </header>

      <ul className="mt-3 space-y-3">
        {topics.map((topic) => (
          <li key={topic.id}>
            <Link
              to={meetingUrl({ meetingId: props.hit.meeting_id, page: topic.page_no, topicId: topic.id })}
              className="group block"
            >
              <span className="font-medium group-hover:underline">
                {topic.item_number && <span className="text-stone-500">{topic.item_number}. </span>}
                <Highlight text={topic.title} />
              </span>
              {topic.has_decision && (
                <span className="ml-2 rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-800 dark:bg-emerald-900/50 dark:text-emerald-300">
                  päätös
                </span>
              )}
              {topic.page_no && <span className="ml-2 text-xs text-stone-500">s. {topic.page_no}</span>}
              {topic.snippet && (
                <p className="mt-1 text-sm text-stone-600 dark:text-stone-400">
                  <Highlight text={topic.snippet} />
                </p>
              )}
            </Link>
          </li>
        ))}
        {text.map((chunk, i) => (
          <li key={`text-${i}`}>
            <Link
              to={meetingUrl({ meetingId: props.hit.meeting_id, page: chunk.page_no })}
              className="block text-sm text-stone-600 hover:underline dark:text-stone-400"
            >
              <span className="mr-2 text-xs text-stone-500">
                Pöytäkirjan teksti{chunk.page_no && `, s. ${chunk.page_no}`}:
              </span>
              <Highlight text={chunk.snippet} />
            </Link>
          </li>
        ))}
      </ul>
    </article>
  );
}
