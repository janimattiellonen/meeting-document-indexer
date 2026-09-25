"""HTTP API for the frontend. Binds to 127.0.0.1 only (see cli.serve)."""

import mimetypes
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import FileResponse
from psycopg_pool import ConnectionPool

from meeting_indexer import boards, db, people, search
from meeting_indexer.config import Settings, get_settings
from meeting_indexer.llm import MeetingType


def connection(request: Request) -> Iterator[psycopg.Connection]:
    with request.app.state.pool.connection() as conn:
        yield conn


Conn = Annotated[psycopg.Connection, Depends(connection)]
# Bounded so the value always fits the SQL int it is compared as; the frontend accepts the same range.
Year = Annotated[int | None, Query(ge=1900, le=2999)]
router = APIRouter(prefix="/api")


@dataclass
class SearchResponse:
    query: str
    results: list[search.MeetingHit]


@router.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@router.get("/search")
def search_meetings(
    conn: Conn,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    year_from: Year = None,
    year_to: Year = None,
    type: MeetingType | None = None,
    sort: search.Sort = "relevance",
) -> SearchResponse:
    """Meetings whose agenda items or text match the query. Snippets mark matches with \\x02…\\x03."""
    results = search.search(conn, q, year_from=year_from, year_to=year_to, meeting_type=type, sort=sort)
    return SearchResponse(query=q, results=results)


@router.get("/meetings")
def meetings(conn: Conn) -> list[db.MeetingSummary]:
    return db.list_meetings(conn)


@router.get("/meetings/{meeting_id}")
def meeting(conn: Conn, meeting_id: int) -> db.MeetingView:
    found = db.load_meeting_by_id(conn, meeting_id)
    if found is None:
        raise HTTPException(404, "meeting not found")
    return found


@dataclass
class PersonSummary:
    id: int
    name: str
    meetings: int  # attended
    first_year: int | None
    last_year: int | None
    board_years: list[int]


@dataclass
class PersonResponse(people.PersonDetail):
    board_terms: list[boards.BoardTerm]


@router.get("/people")
def list_people(
    conn: Conn, q: Annotated[str | None, Query(min_length=1, max_length=100)] = None
) -> list[PersonSummary]:
    """People who attended at least one meeting. q matches any spelling of the name."""
    on_board = boards.members_by_year(conn)
    return [
        PersonSummary(p.id, p.name, p.meetings, p.first_year, p.last_year, on_board.get(p.id, []))
        for p in people.list_people(conn, q)
    ]


@router.get("/people/{person_id}")
def person(conn: Conn, person_id: int) -> PersonResponse:
    found = people.load_person(conn, person_id)
    if found is None:
        raise HTTPException(404, "person not found")
    return PersonResponse(**vars(found), board_terms=boards.person_terms(conn, person_id))


@router.get("/boards")
def board_years(conn: Conn) -> list[boards.BoardYear]:
    """The years with board meetings, oldest first."""
    return boards.board_years(conn)


@router.get("/boards/{year}")
def board(conn: Conn, year: Annotated[int, PathParam(ge=1900, le=2999)]) -> boards.Board:
    """The board of a year, inferred from the attendance of that term's board meetings."""
    found = boards.board(conn, year)
    if found is None:
        raise HTTPException(404, "no board meetings for that year")
    return found


@router.get("/documents/{document_id}/file", response_class=FileResponse)
def document_file(request: Request, conn: Conn, document_id: int) -> FileResponse:
    """The original document, shown inline by the browser when it can (PDF)."""
    rel_path = db.document_rel_path(conn, document_id)
    if rel_path is None:
        raise HTTPException(404, "document not found")
    docs_root: Path = request.app.state.docs_root
    path = (docs_root / rel_path).resolve()
    # rel_path comes from the database, but never serve anything outside the documents folder.
    if not path.is_relative_to(docs_root) or not path.is_file():
        raise HTTPException(404, "file not found")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name, content_disposition_type="inline")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    pool = ConnectionPool(
        settings.database_url, min_size=1, max_size=4, kwargs={"autocommit": True}, open=False
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        pool.open()
        yield
        pool.close()

    # No /docs or /redoc: their pages load scripts from a CDN, and the browser would contact it.
    app = FastAPI(title="Meeting indexer", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.pool = pool
    app.state.docs_root = settings.docs_root.resolve()
    app.include_router(router)
    return app
