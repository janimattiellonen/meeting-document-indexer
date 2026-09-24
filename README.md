# Meeting indexer

A searchable index of the association's meeting minutes (PDF and Word). A local language model reads
each document and extracts:

- the date, time and location;
- who was present and absent;
- every agenda item, with its decisions;
- a summary.

The results go into a database, and a web app lets you search them and open the original document at
the right page.

**Everything runs on your own computer.** The language model, the database and the web app are all
local. The servers listen only on `127.0.0.1`, and no document content leaves the machine.

The design, decisions and roadmap are in [`docs/PLAN.md`](docs/PLAN.md).

## How it fits together

```
data/documents/ ──► indexer (mi index) ──► Ollama (local LLM)
                          │
                          ▼
                  PostgreSQL (Docker) ◄── API (mi serve) ◄── web app (pnpm dev) ◄── browser
```

| Part | Where | Runs on |
|---|---|---|
| Database: PostgreSQL 17 + pgvector | Docker | `127.0.0.1:5434` |
| Indexer and API: Python, FastAPI (`mi`) | `backend/` | API on `127.0.0.1:8000` |
| Web app: React Router, TypeScript | `frontend/` | `http://127.0.0.1:5180` |
| Language model: Ollama | natively on macOS (Docker has no GPU access on a Mac) | `127.0.0.1:11434` |

## Requirements

- **macOS on Apple silicon**, with enough memory for the model. The default 27B model uses about
  18 GB.
- **[Homebrew](https://brew.sh)**
- **[Docker Desktop](https://www.docker.com/products/docker-desktop/)**, for the database.
- **[Ollama](https://ollama.com)** with:
  - a language model: `qwen3.8:27b-mlx` by default, set with `LLM_MODEL` in `.env`;
  - the `bge-m3` embedding model, used by the planned meaning-based search.
- **[uv](https://docs.astral.sh/uv/)**, for Python. It installs Python 3.13 by itself.
- **Node.js 22 and [pnpm](https://pnpm.io)**, for the web app.
- **Voikko**, which gives Finnish base forms for search (`brew install libvoikko`).
- *Optional:* **LibreOffice**, a fallback for old `.doc` files that macOS `textutil` can't read.

## Installation (once)

```bash
git clone git@github.com:janimattiellonen/meeting-document-indexer.git
cd meeting-document-indexer

# Tools
brew install libvoikko
ollama pull bge-m3                      # and your LLM, if it isn't installed yet

# Settings: a copy of .env.example with a random database password (.env is never committed)
sed "s/change-me/$(openssl rand -hex 16)/g" .env.example > .env

# Database: starts PostgreSQL and creates the tables
docker compose up -d

# Backend and web app dependencies
(cd backend && uv sync)
(cd frontend && pnpm install)

# Git hooks: block real meeting content from being committed, and check code style
uv run --project backend pre-commit install
```

Check the setup:

```bash
cd backend
uv run mi status
```

Every line should say `OK`: database, Ollama, both models, Voikko and the documents folder.

### Keeping real content out of git

The repository is public. The documents, and everything derived from them, live in `data/`, which is
never committed. The commit hook blocks:

- anything under `data/`;
- document files outside `tests/fixtures/`;
- `.env` files;
- files containing a name from `data/guard/names.txt`.

For that last check, create `data/guard/names.txt` with one name per line (for example the board
members). If the file is missing, the name check is skipped.

## Indexing documents

Put the minutes in `data/documents/`. Subfolders are fine. Supported formats: `.pdf`, `.docx`,
`.doc`, `.rtf` and `.odt`.

```bash
cd backend
uv run mi index          # processes new and changed documents
uv run mi status         # what's indexed, pending, failed, timed out or scanned
```

- **Time:** about 1–2 minutes per document with the 27B model. A few hundred documents is an
  overnight job. Later runs skip unchanged files.
- **Time limit:** each document gets at most 10 minutes (`DOC_TIME_LIMIT_SECONDS`). A document that
  fails or times out is recorded, and the run continues.
- **Retries:** failed or timed-out documents are not retried automatically. Use
  `mi index --retry-failed`, or `mi reindex <file>`.
- **When a run stops:** only for problems that aren't about one file, such as Ollama not responding
  or 3 failures in a row. Files it didn't reach stay listed as pending in `mi status`.
- **Logs:** each run writes one to `data/logs/`.
- **Scans:** scanned PDFs without a text layer are listed as "no text layer". OCR is planned.

Check one document's extraction:

```bash
uv run mi show "Hallituksen kokous 3 2019.pdf"   # path relative to data/documents/, or a full path
```

## Running the app

The app has two parts, each in its own terminal. The database must be running (`docker compose up -d`),
and so must Ollama.

**Server: the API**

```bash
cd backend
uv run mi serve          # http://127.0.0.1:8000 (--port to change)
```

**Client: the web app**

```bash
cd frontend
pnpm dev                 # http://127.0.0.1:5180
```

Open **http://127.0.0.1:5180**. The dev server forwards `/api` to the API.

- **Haku (search):**
  - searches agenda items, decisions and the full text of the minutes;
  - word forms don't matter: *hallitus* also finds *hallituksen*, and *kisa* finds
    *seuramestaruuskisat*;
  - you can limit by year, and sort by relevance, oldest first ("when did this first come up") or
    newest first;
  - every search is in the address, so it can be bookmarked.
- **Kokoukset (meetings):** all meetings by year.
- **A meeting:** attendance, summary and agenda items with decisions, next to the original PDF.
  Coming from a search result, it opens at the agenda item and at its page in the PDF.

The extracted data can contain mistakes. The original document is always the source.

**A production build** is a static app: `pnpm build` writes it to `frontend/build/client/`. Serving it
together with the API is planned (see `docs/PLAN.md`, Phase 8). For now, use `pnpm dev`.

## Command reference

Run the `mi` commands from `backend/`, as `uv run mi …`. Add `-v` for detailed logging.

| Command | What it does |
|---|---|
| `mi status` | Checks the setup. Lists pending, failed, timed-out and scanned documents, and compares the disk with the database |
| `mi index [paths…]` | Extracts new and changed documents. `--retry-failed`, `--time-limit N`, `--force` |
| `mi reindex <paths…>` | Re-extracts documents even if they haven't changed |
| `mi show <file>` | Prints what was extracted from a document |
| `mi eval` | Measures extraction accuracy against hand-checked files in `data/eval/`. `--init <file>` writes a draft to correct |
| `mi relemmatize` | Computes the search base forms for text already in the database, without the LLM. `--all` recomputes everything |
| `mi serve` | Runs the API on `127.0.0.1:8000` |
| `mi openapi <file>` | Writes the API schema, used to generate the web app's types |

Settings live in `.env` (see [`.env.example`](.env.example)):
- **Documents folder:** `DOCS_ROOT`.
- **Models:** `LLM_MODEL` and `EMBED_MODEL`.
- **Time limits:** `DOC_TIME_LIMIT_SECONDS` and `MAX_CONSECUTIVE_FAILURES`.
- **Addresses:** the database and Ollama addresses must point to this machine; the backend refuses
  to start otherwise.

## Development

```bash
# Backend (in backend/)
uv run pytest                    # database tests need `docker compose up -d`; they use a throwaway database
uv run ruff check . && uv run ruff format --check . && uv run pyright

# Web app (in frontend/)
pnpm test && pnpm lint && pnpm format:check && pnpm typecheck
pnpm gen:api                     # after an API change: regenerate app/api/schema.d.ts
```

- **Tests** use invented names and content only. Test documents are generated in the tests, never
  real minutes.
- **Database changes** are plain SQL files in `db/migrations/`, run by
  [dbmate](https://github.com/amacneil/dbmate). Apply new ones with `docker compose run --rm migrate`.
  `docker compose up` also applies them.

## Data and backups

- **Originals:** the documents stay in `data/documents/` and are never modified.
- **Extracted data:** stored in the Docker volume `meeting-indexer_pgdata`. It survives
  restarts and `docker compose down`, but **`docker compose down -v` deletes it**.
- **Rebuilding:** everything can be rebuilt from the documents with `mi index`, at the cost of the
  indexing time. Database backups are planned.

## Troubleshooting

| Problem | Fix |
|---|---|
| `mi status`: database FAIL | `docker compose up -d`, and check that Docker Desktop is running |
| `mi status`: ollama FAIL | Start Ollama. Check that `OLLAMA_HOST` in `.env` is `http://127.0.0.1:11434` |
| `mi status`: voikko FAIL | `brew install libvoikko` |
| `mi status`: "… lack base forms; run `mi relemmatize`" | `uv run mi relemmatize` |
| The web app says the server isn't responding | Start the API: `uv run mi serve` in `backend/` |
| `pnpm dev`: port 5180 in use | Another app is using it; stop it, or change `server.port` in `frontend/vite.config.ts` |
