# Meeting Indexer – Implementation Plan

Searchable index of Puskasoturit ry meeting minutes (PDF / Word). Everything runs on the local
machine; no document content ever leaves it.

**Status:**
- **Phase 2** (persisted indexing): done on branch `phase-2-indexing`; still waiting for a run over the full set of documents.
- **Phases 3–4 (thin slice):** on branch `phase-3-4-search-ui`:
  - a search API (Postgres full-text search, prefix matching, stemmed or as typed);
  - the React Router SPA with search, a meeting list, and a meeting page with the PDF.
  - Voikko lemmatisation (§8) is still to do.

**Running it locally:**

```bash
docker compose up -d                   # database
cd backend && uv run mi serve          # API on 127.0.0.1:8000
cd frontend && pnpm dev                # app on http://127.0.0.1:5180
```

After an API change, `pnpm gen:api` in `frontend/` regenerates the TypeScript types.

### Known facts about the corpus

| | |
|---|---|
| Size | ~100–200 documents |
| Years | 2006–2026 |
| Formats | `.pdf` and legacy Word `.doc` (possibly some `.docx`) |
| Location | `data/documents/` in this project (`DOCS_ROOT`) – gitignored, never committed |
| Meeting types | Board meetings only for now; general meetings (kevät-/syyskokous) may be added later |
| Repository | <https://github.com/janimattiellonen/meeting-document-indexer> – **public** |

What this means for the plan:

- **Indexing time:** ~200 × 80 s ≈ 4.5 h for a full run with the 27B model. That's fine
  overnight, and later runs only process new or changed files.
- **`.doc` is a primary format.** `textutil` extraction from legacy Word must be tested in
  Phase 2 with real old files, not just assumed to work.
- **Older PDFs (2006–) may be scans.** Phase 2 reports how many documents have no text layer.
  If there are many, OCR moves from Phase 8 forward to right after Phase 2.
- **Board membership** comes from board meeting attendance and roles until general meeting
  minutes are added. The schema already supports general meetings, so adding them later
  needs no migration.
- **The repository is public.** Real document content must never enter git – see §13.

---

## 1. Goals

- Extract per document: meeting title/type, date, time, location, people present and absent
  (with roles), topics (agenda items) with short descriptions and decisions, overall summary.
- Answer questions like:
  - *When did the idea of renewing the club web site first come up?* → earliest matching topics.
  - *When did the board choose the t-shirt manufacturer?* → decision search.
  - *Who were the board members in 2019?* → structured query over attendance and roles.
- Always show the originating document; the document is the source of truth, extracted data
  is only a way to find it.

### Non-goals (for now)

- Multi-user access, authentication, hosting anywhere other than this machine.
- Editing documents.
- Perfect extraction. Extracted data can be wrong; the UI always links to the source.

### Hard constraints

- **Local only.** LLM, embeddings and reranker run locally. Ports are bound to `127.0.0.1`.
  No cloud APIs, no telemetry.
- **Originals are read-only.** The system never writes to the documents directory.

---

## 2. Architecture

```
                       ┌──────────────────────────── macOS host ───────────────────────────┐
                       │                                                                    │
  documents dir (ro) ──┼──► Indexer CLI (Python) ──┐                                        │
                       │                           │  structured extraction, embeddings    │
                       │                           ├──────────────► Ollama (native, Metal)  │
                       │   FastAPI (Python) ───────┤                 qwen3.8:27b-mlx         │
                       │     ▲   shares code with  │                 bge-m3 (embeddings)     │
                       │     │   the indexer       │                                        │
                       │     │                     └──► PostgreSQL 17 + pgvector (Docker)   │
                       │     │ /api (JSON)               127.0.0.1:5434                      │
                       │     │                                                              │
                       │   React Router 8 SPA (TypeScript) ◄── browser                      │
                       └────────────────────────────────────────────────────────────────────┘
```

- **Indexer and API are one Python package.** They share models, DB access, embedding and text
  normalisation, so a query is always embedded exactly like the indexed content.
- **Ollama runs natively**, because Docker on macOS has no GPU access (MLX would fall back to CPU).
  Containers reach it through `host.docker.internal:11434`.
- **The frontend is a static SPA** (React Router 8, `ssr: false`) that talks only to the API.

---

## 3. Tech stack

| Area | Choice | Notes |
|---|---|---|
| LLM | `qwen3.8:27b-mlx` via Ollama | Structured output via JSON schema, `think=false`, temperature 0 |
| Embeddings | `bge-m3` via Ollama (1024 dims) | Multilingual, good Finnish support |
| Reranker | `BAAI/bge-reranker-v2-m3` via `sentence-transformers` (MPS) | Phase 5; Ollama can't run cross-encoders |
| Database | PostgreSQL 17 + `pgvector` + `pg_trgm` | Docker image `pgvector/pgvector:pg17` |
| Full-text search | Postgres `finnish` text search config | Stems regular inflections only – see §8 |
| Migrations | `dbmate`, plain SQL files | Language-agnostic, runs as a compose service |
| Backend | Python 3.13, `uv`, FastAPI, `psycopg` 3, Pydantic v2 | Hand-written SQL; search queries are too specific for an ORM |
| Document text | PyMuPDF, python-docx, macOS `textutil` (.doc/.rtf/.odt) | OCR later with `ocrmypdf` |
| CLI | `typer` | `mi index`, `mi status`, … |
| Frontend | React Router 8 (SPA mode), TypeScript strict, Vite, Tailwind, shadcn/ui | Finnish UI |
| API types | `openapi-typescript` + `openapi-fetch` | Types generated from FastAPI's OpenAPI schema |
| Quality | ruff, pyright, pytest · ESLint, Prettier, Vitest, Testing Library | |

Correction to the earlier discussion: **don't use `unaccent`** on Finnish text – it would turn
`ä`/`ö` into `a`/`o` and merge distinct words (e.g. *sää* / *saa*).

---

## 4. Repository layout

```
meeting-indexer/
├── docker-compose.yaml
├── .env.example                 # committed; .env is gitignored
├── db/
│   └── migrations/              # dbmate; the first one also creates the vector and pg_trgm extensions
├── backend/
│   ├── pyproject.toml
│   ├── src/meeting_indexer/
│   │   ├── config.py            # settings from env (pydantic-settings)
│   │   ├── extract/             # file discovery, hashing, text extraction per page
│   │   ├── llm/                 # prompts, schemas (Meeting, Topic, Person), Ollama client
│   │   ├── embed.py             # the ONE embedding function used by indexer and API
│   │   ├── people.py            # name normalisation and alias matching
│   │   ├── db/                  # connection pool, repositories (SQL)
│   │   ├── search/              # full-text, vector, hybrid (RRF), rerank
│   │   ├── indexer.py           # pipeline orchestration
│   │   ├── cli.py               # typer app: `mi …`
│   │   └── api/                 # FastAPI app and routers
│   └── tests/
├── frontend/                    # React Router 8 SPA
│   └── app/
│       ├── routes/
│       ├── api/                 # generated schema.d.ts + openapi-fetch client
│       └── components/
├── scripts/backup.sh            # pg_dump to data/backups/
├── data/                        # GITIGNORED – real content lives only here
│   ├── documents/               # DOCS_ROOT: the meeting minutes
│   ├── eval/                    # golden set: hand-checked expected output
│   └── backups/
└── docs/PLAN.md
```

---

## 5. Docker Compose

| Service | Image / build | Ports | Notes |
|---|---|---|---|
| `db` | `pgvector/pgvector:pg17` | `127.0.0.1:5434:5432` | Named volume `pgdata`, healthcheck `pg_isready` |
| `migrate` | `ghcr.io/amacneil/dbmate` | – | Runs `dbmate up`, `depends_on: db (healthy)` |
| `api` | `backend/Dockerfile` | `127.0.0.1:8000:8000` | Profile `app`; docs dir mounted `:ro` at `/docs` |
| `web` | `frontend/Dockerfile` (static build served by nginx or by FastAPI) | `127.0.0.1:5180:80` | Profile `app` |

- **Development:** only `db` and `migrate` run in Docker. API, indexer and frontend run natively
  (`uv run …`, `pnpm dev`) for a fast feedback loop. Vite (port 5180, since 5173 is used by other projects here) proxies `/api` to `localhost:8000`,
  so there's no CORS setup.
- **"Appliance" mode:** `docker compose --profile app up` runs everything except Ollama. Inside
  compose, the API reaches the database as host `db`, so the local-host check in `config.py`
  must also accept the compose service name then.
- Ports 5432 and 5433 are already used by other containers on this machine, hence 5434.
- `.env`: `POSTGRES_*`, `DATABASE_URL`, `DOCS_ROOT` (default `./data/documents`), `OLLAMA_HOST`,
  `LLM_MODEL`, `EMBED_MODEL`.

---

## 6. Data model

All document paths are stored **relative to `DOCS_ROOT`**, so they work the same on the host
and inside containers, and survive moving the folder.

```sql
documents (
  id            bigserial primary key,
  rel_path      text unique not null,
  sha256        text not null,            -- skip unchanged files
  file_type     text not null,            -- pdf, docx, doc, …
  page_count    int,
  status        text not null,            -- pending | indexed | no_text | failed
  error         text,
  extractor_version text,                 -- prompt + schema version; bump → re-extract
  llm_model     text,
  indexed_at    timestamptz
)

document_pages (
  document_id   bigint references documents on delete cascade,
  page_no       int,
  text          text,
  primary key (document_id, page_no)
)

meetings (
  id            bigserial primary key,
  document_id   bigint unique references documents on delete cascade,
  title         text not null,            -- "Hallituksen kokous 4/2026"
  meeting_type  text not null,            -- board | spring_general | autumn_general | extraordinary | other
  meeting_date  date,
  start_time    time,
  end_time      time,
  location      text,
  summary       text,
  raw_extraction jsonb not null,          -- full LLM output, for debugging and re-processing
  search_tsv    tsvector generated always as
                (to_tsvector('finnish', coalesce(title,'') || ' ' || coalesce(summary,''))) stored
)

people (
  id            bigserial primary key,
  canonical_name text unique not null
)
person_aliases (
  alias         text primary key,         -- every spelling seen, e.g. "M. Meikäläinen"
  person_id     bigint references people
)

attendance (
  meeting_id    bigint references meetings on delete cascade,
  person_id     bigint references people,
  status        text not null,            -- present | absent
  role          text,                     -- puheenjohtaja, sihteeri, …
  name_as_written text not null,
  primary key (meeting_id, person_id)
)

topics (
  id            bigserial primary key,
  meeting_id    bigint references meetings on delete cascade,
  ordinal       int not null,             -- order in the document
  item_number   text,                     -- "5", "5a" as written
  title         text not null,
  summary       text,
  decisions     text,                     -- what was decided, if anything
  page_no       int,                      -- for deep links: file.pdf#page=N
  search_tsv    tsvector generated always as (
                  setweight(to_tsvector('finnish', title), 'A') ||
                  setweight(to_tsvector('finnish', coalesce(decisions,'')), 'B') ||
                  setweight(to_tsvector('finnish', coalesce(summary,'')), 'C')) stored,
  embedding     vector(1024)
)

chunks (                                  -- raw text, so search doesn't rely only on LLM summaries
  id            bigserial primary key,
  document_id   bigint references documents on delete cascade,
  page_no       int,
  text          text not null,
  search_tsv    tsvector generated always as (to_tsvector('finnish', text)) stored,
  embedding     vector(1024)
)
```

Indexes: GIN on every `search_tsv`, HNSW (`vector_cosine_ops`) on every `embedding`,
GIN `gin_trgm_ops` on `topics.title` and `person_aliases.alias`, B-tree on `meetings.meeting_date`.

**Board members for a year** come from `attendance` of `board` meetings in that year, with roles.
"That year" means the board's term, taken from the meeting number ("1/2026"), not the meeting date:
a new board can hold its first meeting before the year starts (meeting 1/2026 was held on
8.12.2025).
Later, general meetings (syyskokous) can also be extracted for *elected* officials, which is
more authoritative than attendance.

---

## 7. Indexing pipeline

`mi index` (default `DOCS_ROOT`) runs these steps for each file:

1. **Discover** supported files and skip Office lock files (`~$…`).
2. **Hash** the file. Skip it if `sha256`, `extractor_version` and the model are unchanged
   (`--force` overrides). Failed and timed-out documents are *not* retried automatically; see
   "Limits and failures" below.
3. **Extract text per page.** Pages are kept separate so topics can be linked to a page. If
   there's no text layer, set `status = no_text` (OCR comes in Phase 8).
4. **LLM extraction** with the Pydantic schema as `format`. Page markers (`[sivu 2]`) are
   included in the prompt text. The context window grows with the document (8k–64k tokens).
   A longer document fails with a clear error instead of being cut off silently; splitting it
   into sections is only worth building if that ever happens.
   - **Output is capped at 8,192 tokens, with a 15-minute timeout.** In Phase 2 the model once
     emitted filler endlessly (a failure mode of format-constrained output). With the cap, that
     fails one document instead of hanging the run.
   - **The model is asked for as little structure as possible.** A separate item-number field is
     what triggered the endless filler. The model copies titles as written ("5. Otsikko"), and
     code splits off the number and strips the association letterhead from the meeting title.
5. **Validate and repair.** Parse the date, and fall back to the first date on the first page when
   the model gives none or something implausible. Check that topic titles actually appear in the
   text (fuzzy match), and assign `page_no` by that match instead of trusting the model.
6. **People.** Match each name to `person_aliases`, exactly first (ignoring case), then with
   trigram similarity ≥ 0.8. Unmatched names create a new person.
   - The threshold is deliberately conservative. A one-letter typo (*Meikäläinen* /
     *Meikälainen*) scores 0.70, but two different people (*Mikko* / *Mika Virtanen*) score 0.71,
     so no threshold separates them.
   - Wrongly merging two people is worse than one person having two spellings, which
     `mi people merge` fixes by hand (Phase 6).
7. **Embed** topics (title + decisions + summary) and chunks (~800-token windows of page text)
   using `embed.py`.
8. **Write** everything for the document in one transaction: delete the old rows, insert the new ones.
   Connections use autocommit, so each document's transaction commits on its own. Otherwise
   psycopg's implicit transaction would hold every document until the run ends.

- Progress is shown with the elapsed time per document and an estimated time remaining.
  At ~90 s/document, a few hundred documents is an overnight job.

### Limits and failures

One document must never hold up a run, and nothing a run skips may go unrecorded.

- **Hard time limit per document: 10 minutes by default** (`DOC_TIME_LIMIT_SECONDS`, or
  `mi index --time-limit N`). Reading the file and the LLM extraction run in a worker process
  (`limits.py`), which is killed when the limit is reached. That holds whichever step hangs,
  including C code such as PyMuPDF. When the worker is killed, Ollama cancels the request too
  ("Request terminated … context canceled" in its log), so nothing keeps generating in the
  background. 10 minutes is ~6× a normal document and covers the longest legitimate generation
  (the 8,192-token output cap at ~21 tokens/s ≈ 7 min).
- **A document that fails or times out is recorded and the run continues.** Its row gets status
  `failed` or `timed_out`, the error, the attempt count, the time of the last attempt and how
  long it took. Any earlier extraction of the document is kept.
- **No automatic retries.** Retrying would spend the full limit on the same file on every run.
  Failed and timed-out documents are retried when the file changes, with
  `mi index --retry-failed`, or with `mi reindex <path>`.
- **The run stops only for problems that aren't about one file:**
  - Ollama doesn't respond after a failure, or
  - 3 documents in a row fail (`MAX_CONSECUTIVE_FAILURES`), which points at the system.

  Without this, a hung Ollama would cost the full limit for every remaining document.
- **The gaps are always visible:**
  - Every file is registered as `pending` before processing starts, so files a stopped or
    crashed run never reached stay listed.
  - `mi status` lists pending, timed-out, failed and scanned documents. It also compares the
    disk with the database: files not registered yet, files changed since indexing, and files
    that are gone from disk.
- **Every run writes a log file** to `data/logs/index-<time>.log` (gitignored). It records each
  document's outcome, full error tracebacks (including those from the worker process), and the
  names of documents not reached if the run stopped.
- Other commands: `mi status` (counts per status, lists failed and scanned documents),
  `mi reindex <path>`, `mi show <path>` (prints one document's extraction), and
  `mi people list|merge <a> <b>` (Phase 6).

---

## 8. Search

- **Full-text (built in the Phase 3–4 slice, `search/__init__.py`):**
  - Each query word (letters, digits and inner hyphens, at least two characters) is matched as a
    prefix in two forms: stemmed (`to_tsquery('finnish', 'word:*')`) and as typed (`simple`).
    The stemmer turns *verkko* into *verko*, which is not a prefix of *verkkosivut*.
  - All words must match. Finnish stopwords (*ja*, *on*) are dropped, because the index leaves
    them out.
  - There are no search operators: `OR`, quotes and a leading `-` are read as ordinary words.
    `websearch_to_tsquery` would interpret them, but it can't match prefixes.
  - Searches `topics` (title, summary, decisions) and `chunks` (the raw text), ranked with
    `ts_rank_cd`; a match in the raw text counts half. `meetings.search_tsv` (meeting title and
    summary) is not queried yet.
- **Known limitation, found in Phase 1:** the Snowball `finnish` stemmer handles regular endings
  (*verkkosivut* = *verkkosivuilla*), but misses stem changes and consonant gradation:
  *hallitus* ≠ *hallituksen*, *kokous* ≠ *kokouksessa*, *kisa* ≠ *kisoille*,
  *paita* ≠ *paidat* ≠ *paitoja*. That affects many everyday words.
  *Fix (Phase 3):* lemmatise text with **Voikko** (`libvoikko`, a local Finnish morphological
  analyser, available via Homebrew and as a Python binding). Lemmatise both the indexed text and
  the query to base forms, and store the result in `tsvector` columns using the `simple` config.
  Compound words are split into their parts as well (*kotisivu-uudistus* → *kotisivu*,
  *uudistus*). This needs a migration that changes the generated `search_tsv` columns into
  ordinary columns filled by the indexer. Embeddings (Phase 5) and trigram matching cover the
  cases lemmatisation still misses.
- **Semantic:** `embed(q)`, then cosine distance on `topics.embedding` and `chunks.embedding`.
  This catches synonyms and compound words (*nettisivut* ↔ *kotisivu-uudistus*).
- **Hybrid:** merge both lists with Reciprocal Rank Fusion (k = 60) and group hits by meeting.
- **Rerank (Phase 5):** the top 50 go through `bge-reranker-v2-m3`, and the top 20 are returned.
- **Filters:** date range / year, meeting type, person (attended), has-decision.
- **Sort:** relevance (default) or **date ascending**, which answers "when did this first come up".
- Each hit returns the meeting, the matching topic with a highlighted snippet (`ts_headline`),
  the page number and a document link.

---

## 9. API (FastAPI)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/search?q=&year_from=&year_to=&type=&person=&sort=` | Hybrid search, grouped by meeting |
| GET | `/api/meetings?year=&type=` | Meeting list (timeline) |
| GET | `/api/meetings/{id}` | Full meeting: attendance, topics, summary |
| GET | `/api/documents/{id}/file` | Streams the original file (`inline`; path must resolve inside `DOCS_ROOT`) |
| GET | `/api/people?q=` | People with attendance counts |
| GET | `/api/people/{id}` | Person: roles by year, meetings attended |
| GET | `/api/board?year=` | Board members and roles for a year |
| POST | `/api/ask` | Question → answer with cited topics (Phase 7) |
| POST | `/api/index` · GET `/api/index/status` | Start indexing from the UI and follow progress (Phase 8) |

- Built so far: `/api/search` with `q` (1–200 characters), `year_from`/`year_to` (1900–2999),
  `type` and `sort` (`relevance`, `oldest`, `newest`), full-text only; `/api/meetings` without
  filters; `/api/meetings/{id}`; `/api/documents/{id}/file`. `person` comes with the people pages.
- The API binds to `127.0.0.1` only.
- The OpenAPI schema is exported to `frontend/app/api/schema.d.ts` with a `pnpm gen:api` script.

---

## 10. Frontend (React Router 8 SPA)

| Route | Content |
|---|---|
| `/` | Search box and filters. Results are grouped by meeting (date, title, matching topics with highlights, "Avaa pöytäkirja" link). All state lives in the URL, with Finnish parameter names (`?q=…&alkaen=…&asti=…&jarjestys=…`) |
| `/kokoukset` | Timeline of meetings by year |
| `/kokoukset/:id` | Meeting details, with the PDF shown next to them (iframe `#page=N`) |
| `/henkilot`, `/henkilot/:id` | People, and each person's roles and attendance over time |
| `/hallitus/:vuosi` | The board for a given year |
| `/kysy` | Q&A: the answer, plus cards linking to the source documents (Phase 7) |

- Data comes through `clientLoader` with the typed `openapi-fetch` client, so there's no extra
  state library.
- The UI is in Finnish. Clicking any topic opens the original document at the right page.

---

## 11. Q&A (Phase 7)

1. Hybrid search with reranking returns the top ~10 topics and chunks.
2. Qwen gets the question and the numbered sources, and must answer **only** from them and
   cite source numbers. If the sources don't answer the question, it says so.
3. The API checks that every citation refers to a provided source and drops anything that doesn't.
4. The UI shows the answer with citation links. Sources are always listed, so the document stays
   the source of truth.

---

## 12. Quality and testing

- **Golden set:** hand-check the output for 8–10 representative documents (spread across
  2006–2026, both `.pdf` and `.doc`). The golden files contain real names and content, so they
  live in `data/eval/` and are never committed. `mi eval` reports per-field accuracy:
  date, location, attendees (precision and recall), topic count, topic titles. Run it whenever
  prompts, schemas or models change. It is also how we compare speed against accuracy for a
  smaller model. `mi eval --init <document>` writes a draft from the current extraction, to be
  corrected by hand against the document.
- **Backend:** pytest unit tests for text extraction, date and name normalisation, and RRF
  merging. Integration tests against a throwaway Postgres (a compose test profile) for
  migrations, repositories and search ranking on a small fixture set. The LLM is mocked except
  in the eval.
- **Frontend:** Vitest + Testing Library for components and loaders, with a mocked API.
- **Search relevance:** a small list of queries with expected meetings (e.g. *nettisivut*,
  *t-paidat*, *hallitus 2019*), checked as a regression test.

---

## 13. Privacy and security checklist

- Every published port uses the `127.0.0.1:` prefix, including Postgres, the API and the web app.
- `OLLAMA_HOST` must point to localhost. The config refuses to start if it doesn't.
- `HF_HUB_OFFLINE=1` is set after the reranker model has been downloaded once.
- The documents directory is mounted `:ro`. The file endpoint rejects paths outside `DOCS_ROOT`
  (resolves symlinks, blocks `..`).
- **The GitHub repository is public**, so nothing derived from real documents may be committed.
  `.gitignore` covers `data/`, all document file types, `.env`, database dumps and
  `index.html`/`scrap.txt` (earlier experiments containing real content). Test fixtures under
  `**/tests/fixtures/` are allowed but must be **synthetic** documents with invented names.
- A pre-commit hook adds a second safety net. It rejects staged document files outside
  `tests/fixtures/`, anything under `data/`, and files containing names from a local
  (gitignored) list of known members.
- Code, prompts and docs must not contain real member names or meeting details. Examples in
  prompts and docs use invented names.
- Some minutes carry confidential attachments (e.g. removed members). They are indexed like any
  other text but stay in the local database. An exclude list in the config can skip files.
- `scripts/backup.sh` runs `pg_dump`. The database can always be rebuilt from the documents,
  so backups are a convenience rather than critical.

---

## 14. Phases

Each phase ends with something usable and a clear check that it works.

| # | Phase | Deliverable | Done when |
|---|---|---|---|
| 0 | Proof of concept ✅ | `poc.py` prints extracted data | Example document extracted correctly |
| 1 | Foundation ✅ | Repo restructure, pre-commit guard, compose (`db`, `migrate`), first migration, config, `mi --help` | `docker compose up` gives a migrated DB on 5434; `mi status` connects; committing a file from `data/` is blocked |
| 2 | Persisted indexing | Pipeline steps 1–6 and 8 (no embeddings yet), `mi index/status/reindex`, golden-set eval | A full real folder indexes, including old `.doc` files; re-running skips unchanged files; eval report produced; count of scanned documents known |
| 3 | Search API | Full-text search, filters, sort by date, meetings, documents and board endpoints | A search for a word from a known document returns that meeting with a working page link |
| 4 | Frontend MVP | Search page, meeting page with PDF, meeting timeline | Finding and opening a document by search works end to end in the browser |
| 5 | Semantic search | Embeddings (step 7), hybrid RRF, reranker | Relevance regression queries pass, including synonym / compound cases |
| 6 | People and boards | Alias matching, `mi people merge`, person and board pages | "Who was on the board in year X" is correct for a couple of checked years |
| 7 | Q&A | `/api/ask` with citations, `/kysy` page | Answers cite real sources; says "not found" when appropriate |
| 8 | Hardening | OCR (`ocrmypdf`), indexing from the UI, `app` compose profile, backups | Scanned PDFs are searchable; `docker compose --profile app up` runs the full system |

---

## 15. Risks and open questions

**Risks**

- **Varied document formats over the years.** Older minutes may use different layouts, or have
  no numbered items. *Mitigation:* the golden set covers the variety, and `raw_extraction` is
  kept so documents can be re-processed.
- **Speed.** At ~80 s per document with the 27B model, a full re-extract after a prompt change
  takes hours. *Mitigation:* `extractor_version` re-extracts only what's needed, and the eval
  tells us whether a smaller or faster model is good enough.
- **Memory.** This machine has 64 GB. The 27B model (~18 GB) plus bge-m3 and the reranker fit
  comfortably, but indexing and heavy API use at the same time will contend for the GPU.
- **Inconsistent names.** Nicknames, typos and initials. *Mitigation:* the alias table,
  trigram matching and manual merge.
- **Scanned documents** don't work until OCR is added (Phase 8, or earlier if Phase 2 shows
  many scans).
- **Legacy `.doc` from the mid-2000s.** `textutil` may lose structure or fail on some files.
  *Fallback:* LibreOffice headless (`soffice --convert-to txt`), which runs locally too.
- **Accidental leak through the public repository.** *Mitigation:* `.gitignore`, the
  pre-commit guard, synthetic-only fixtures (§13).

**Open questions**

1. Should the UI be Finnish only?
2. Should the GitHub repository stay public? A private repository removes the leak risk
   entirely. The safeguards in §13 apply either way.
