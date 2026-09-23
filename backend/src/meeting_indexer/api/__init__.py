"""HTTP API for the frontend. Binds to 127.0.0.1 only (see cli.serve)."""

import mimetypes
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from psycopg_pool import ConnectionPool

from meeting_indexer import db, search
from meeting_indexer.config import Settings, get_settings
from meeting_indexer.llm import MeetingType


def connection(request: Request) -> Iterator[psycopg.Connection]:
    with request.app.state.pool.connection() as conn:
        yield conn


Conn = Annotated[psycopg.Connection, Depends(connection)]
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
    year_from: int | None = None,
    year_to: int | None = None,
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
