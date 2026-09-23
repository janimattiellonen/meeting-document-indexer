"""The indexing pipeline: file → text → LLM extraction → validation → database."""

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import psycopg

from meeting_indexer import db
from meeting_indexer.extract import extract_pages, has_text, sha256
from meeting_indexer.llm import EXTRACTOR_VERSION, Meeting
from meeting_indexer.normalize import (
    Warnings,
    chunk_pages,
    clean_name,
    clean_role,
    clean_title,
    meeting_date,
    parse_time,
    split_item_number,
    topic_pages,
)
from meeting_indexer.people import resolve_person

log = logging.getLogger(__name__)

ExtractFn = Callable[[list[str]], Meeting]


@dataclass
class Result:
    rel_path: str
    status: str  # indexed | no_text | failed | skipped
    seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


def is_up_to_date(stored: db.StoredDocument | None, digest: str, model: str) -> bool:
    if stored is None or stored.sha256 != digest:
        return False
    if stored.status == "no_text":
        return True
    return (
        stored.status == "indexed"
        and stored.extractor_version == EXTRACTOR_VERSION
        and stored.llm_model == model
    )


def index_file(
    conn: psycopg.Connection,
    path: Path,
    root: Path,
    extract: ExtractFn,
    model: str,
    *,
    force: bool = False,
    today: date | None = None,
) -> Result:
    rel_path = path.resolve().relative_to(root.resolve()).as_posix()
    file_type = path.suffix.lower().lstrip(".")
    started = time.monotonic()

    try:
        digest = sha256(path)
        if not force and is_up_to_date(db.get_document(conn, rel_path), digest, model):
            return Result(rel_path, "skipped")
        pages = extract_pages(path)

        if not has_text(pages):
            with conn.transaction():
                db.save_document(
                    conn, rel_path=rel_path, sha256=digest, file_type=file_type, pages=pages, status="no_text"
                )
            return Result(rel_path, "no_text", time.monotonic() - started)

        # The LLM call takes minutes; no transaction is held while it runs.
        meeting = extract(pages)
        warnings = Warnings()
        with conn.transaction():
            store(conn, rel_path, digest, file_type, pages, meeting, model, warnings, today or date.today())
        return Result(rel_path, "indexed", time.monotonic() - started, warnings.items)

    except Exception as e:
        log.exception("indexing %s failed", rel_path)
        error = f"{type(e).__name__}: {e}"
        # Keeps any earlier extraction of this document; the next run retries it.
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO documents (rel_path, sha256, file_type, status, error)
                VALUES (%s, %s, %s, 'failed', %s)
                ON CONFLICT (rel_path) DO UPDATE
                SET status = 'failed', error = EXCLUDED.error, updated_at = now()
                """,
                (rel_path, "", file_type, error),
            )
        return Result(rel_path, "failed", time.monotonic() - started, error=error)


def store(
    conn: psycopg.Connection,
    rel_path: str,
    digest: str,
    file_type: str,
    pages: list[str],
    meeting: Meeting,
    model: str,
    warnings: Warnings,
    today: date,
) -> None:
    document_id = db.save_document(
        conn,
        rel_path=rel_path,
        sha256=digest,
        file_type=file_type,
        pages=pages,
        status="indexed",
        extractor_version=EXTRACTOR_VERSION,
        llm_model=model,
    )
    fields = {
        "title": clean_title(meeting.title),
        "meeting_type": meeting.meeting_type,
        "meeting_date": meeting_date(meeting.date, pages[0], warnings, today),
        "start_time": parse_time(meeting.start_time),
        "end_time": parse_time(meeting.end_time),
        "location": meeting.location,
        "summary": meeting.summary,
    }
    numbered = [split_item_number(t.title) for t in meeting.topics]
    page_numbers = topic_pages([title for _, title in numbered], pages, warnings)
    meeting_id = db.insert_meeting(conn, document_id, fields)

    for status, persons in (("present", meeting.present), ("absent", meeting.absent)):
        for person in persons:
            name = clean_name(person.name)
            if not name:
                continue
            person_id = resolve_person(conn, name)
            if not db.insert_attendance(conn, meeting_id, person_id, status, clean_role(person.role), name):
                warnings.add(f"{name!r} is listed more than once in the attendance")

    db.insert_topics(
        conn,
        meeting_id,
        [
            {
                "ordinal": i,
                "item_number": item_number,
                "title": title,
                "summary": topic.summary,
                "decisions": topic.decisions,
                "page_no": page_no,
            }
            for i, (topic, (item_number, title), page_no) in enumerate(
                zip(meeting.topics, numbered, page_numbers, strict=True)
            )
        ],
    )
    db.insert_chunks(conn, document_id, chunk_pages(pages))
    db.set_raw_extraction(conn, meeting_id, {"llm": meeting.model_dump(), "warnings": warnings.items})


def index_paths(
    conn: psycopg.Connection,
    paths: Iterable[Path],
    root: Path,
    extract: ExtractFn,
    model: str,
    *,
    force: bool = False,
    on_result: Callable[[int, int, Result], None] | None = None,
) -> list[Result]:
    files = list(paths)
    results = []
    for i, path in enumerate(files, 1):
        result = index_file(conn, path, root, extract, model, force=force)
        results.append(result)
        if on_result:
            on_result(i, len(files), result)
    return results
