"""Database access."""

from dataclasses import dataclass
from datetime import date, datetime, time

import psycopg
from psycopg.types.json import Jsonb

from meeting_indexer.config import get_settings


def returned_id(cursor: psycopg.Cursor) -> int:
    row = cursor.fetchone()
    assert row is not None, "INSERT … RETURNING returned no row"
    return row[0]


def connect(url: str | None = None) -> psycopg.Connection:
    """Autocommit, so that each `with conn.transaction()` is a real transaction that commits on exit.

    Without it the first query opens an implicit transaction, every later `conn.transaction()` becomes
    a savepoint inside it, and nothing is committed until the connection closes.
    """
    return psycopg.connect(url or get_settings().database_url, connect_timeout=5, autocommit=True)


def document_counts(conn: psycopg.Connection) -> dict[str, int]:
    """Number of documents per indexing status."""
    rows = conn.execute("SELECT status, count(*) FROM documents GROUP BY status").fetchall()
    return {status: count for status, count in rows}


@dataclass
class ProblemDocument:
    rel_path: str
    status: str
    error: str | None
    attempts: int
    last_attempt_at: datetime | None
    duration_seconds: float | None


def problem_documents(conn: psycopg.Connection) -> list[ProblemDocument]:
    """Documents that are not (yet) indexed: pending, failed, timed out or without a text layer."""
    rows = conn.execute(
        """
        SELECT rel_path, status, error, attempts, last_attempt_at, duration_seconds FROM documents
        WHERE status <> 'indexed' ORDER BY status, rel_path
        """
    ).fetchall()
    return [ProblemDocument(*row) for row in rows]


def stored_states(conn: psycopg.Connection) -> dict[str, tuple[str | None, str]]:
    """rel_path -> (sha256, status) of every registered document."""
    return {
        path: (digest, status)
        for path, digest, status in conn.execute("SELECT rel_path, sha256, status FROM documents")
    }


@dataclass
class StoredDocument:
    id: int
    sha256: str | None
    status: str
    extractor_version: str | None
    llm_model: str | None


def get_document(conn: psycopg.Connection, rel_path: str) -> StoredDocument | None:
    row = conn.execute(
        "SELECT id, sha256, status, extractor_version, llm_model FROM documents WHERE rel_path = %s",
        (rel_path,),
    ).fetchone()
    return StoredDocument(*row) if row else None


def register_pending(conn: psycopg.Connection, documents: list[tuple[str, str]]) -> int:
    """Record (rel_path, file_type) of files not seen before as pending. Returns how many were new.

    Done at the start of a run, so files the run never reaches (it was stopped, it crashed) still show up.
    """
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO documents (rel_path, file_type, status) VALUES (%s, %s, 'pending')
            ON CONFLICT (rel_path) DO NOTHING
            """,
            documents,
        )
        return max(cur.rowcount, 0)


def start_attempt(conn: psycopg.Connection, rel_path: str, file_type: str) -> None:
    conn.execute(
        """
        INSERT INTO documents (rel_path, file_type, status, attempts, last_attempt_at)
        VALUES (%s, %s, 'pending', 1, now())
        ON CONFLICT (rel_path) DO UPDATE
        SET attempts = documents.attempts + 1, last_attempt_at = now(), updated_at = now()
        """,
        (rel_path, file_type),
    )


def record_failure(
    conn: psycopg.Connection,
    *,
    rel_path: str,
    file_type: str,
    sha256: str | None,
    status: str,
    error: str,
    duration_seconds: float,
) -> None:
    """Mark a failed or timed-out attempt, counted by start_attempt. Any earlier extraction is kept.

    sha256 is the hash of the file that failed, so it is retried when the file changes but not on every
    run. When the file couldn't even be hashed (sha256 None), the stored hash is kept.
    """
    conn.execute(
        """
        INSERT INTO documents (rel_path, file_type, sha256, status, error, duration_seconds,
                               attempts, last_attempt_at)
        VALUES (%(rel_path)s, %(file_type)s, %(sha256)s, %(status)s, %(error)s, %(duration)s, 1, now())
        ON CONFLICT (rel_path) DO UPDATE SET
            sha256 = coalesce(EXCLUDED.sha256, documents.sha256), status = EXCLUDED.status,
            error = EXCLUDED.error, duration_seconds = EXCLUDED.duration_seconds, updated_at = now()
        """,
        {
            "rel_path": rel_path,
            "file_type": file_type,
            "sha256": sha256,
            "status": status,
            "error": error,
            "duration": duration_seconds,
        },
    )


def save_document(
    conn: psycopg.Connection,
    *,
    rel_path: str,
    sha256: str,
    file_type: str,
    pages: list[str],
    status: str,
    duration_seconds: float | None = None,
    extractor_version: str | None = None,
    llm_model: str | None = None,
) -> int:
    """Insert or update the document row and replace its pages. Deletes any previous extraction."""
    document_id = returned_id(
        conn.execute(
            """
            INSERT INTO documents (rel_path, sha256, file_type, page_count, status, duration_seconds,
                                   extractor_version, llm_model, indexed_at)
            VALUES (%(rel_path)s, %(sha256)s, %(file_type)s, %(page_count)s, %(status)s,
                    %(duration_seconds)s, %(extractor_version)s, %(llm_model)s,
                    CASE WHEN %(status)s = 'indexed' THEN now() END)
            ON CONFLICT (rel_path) DO UPDATE SET
                sha256 = EXCLUDED.sha256, file_type = EXCLUDED.file_type, page_count = EXCLUDED.page_count,
                status = EXCLUDED.status, error = NULL, duration_seconds = EXCLUDED.duration_seconds,
                extractor_version = EXCLUDED.extractor_version, llm_model = EXCLUDED.llm_model,
                indexed_at = EXCLUDED.indexed_at, updated_at = now()
            RETURNING id
            """,
            {
                "rel_path": rel_path,
                "sha256": sha256,
                "file_type": file_type,
                "page_count": len(pages),
                "status": status,
                "duration_seconds": duration_seconds,
                "extractor_version": extractor_version,
                "llm_model": llm_model,
            },
        )
    )

    conn.execute("DELETE FROM meetings WHERE document_id = %s", (document_id,))
    conn.execute("DELETE FROM chunks WHERE document_id = %s", (document_id,))
    conn.execute("DELETE FROM document_pages WHERE document_id = %s", (document_id,))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO document_pages (document_id, page_no, text) VALUES (%s, %s, %s)",
            [(document_id, i, text) for i, text in enumerate(pages, 1)],
        )
    return document_id


def insert_meeting(conn: psycopg.Connection, document_id: int, fields: dict) -> int:
    """The raw extraction is filled in with set_raw_extraction once all warnings are known."""
    return returned_id(
        conn.execute(
            """
            INSERT INTO meetings (document_id, title, meeting_type, meeting_date, start_time, end_time,
                                  location, summary, raw_extraction)
            VALUES (%(document_id)s, %(title)s, %(meeting_type)s, %(meeting_date)s, %(start_time)s,
                    %(end_time)s, %(location)s, %(summary)s, '{}')
            RETURNING id
            """,
            {**fields, "document_id": document_id},
        )
    )


def set_raw_extraction(conn: psycopg.Connection, meeting_id: int, raw: dict) -> None:
    conn.execute("UPDATE meetings SET raw_extraction = %s WHERE id = %s", (Jsonb(raw), meeting_id))


def insert_attendance(
    conn: psycopg.Connection, meeting_id: int, person_id: int, status: str, role: str | None, name: str
) -> bool:
    """False if the person was already recorded for this meeting."""
    cur = conn.execute(
        """
        INSERT INTO attendance (meeting_id, person_id, status, role, name_as_written)
        VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
        """,
        (meeting_id, person_id, status, role, name),
    )
    return cur.rowcount == 1


def insert_topics(conn: psycopg.Connection, meeting_id: int, topics: list[dict]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO topics (meeting_id, ordinal, item_number, title, summary, decisions, page_no)
            VALUES (%(meeting_id)s, %(ordinal)s, %(item_number)s, %(title)s, %(summary)s,
                    %(decisions)s, %(page_no)s)
            """,
            [{**t, "meeting_id": meeting_id} for t in topics],
        )


def insert_chunks(conn: psycopg.Connection, document_id: int, chunks: list[tuple[int, str]]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO chunks (document_id, ordinal, page_no, text) VALUES (%s, %s, %s, %s)",
            [(document_id, i, page_no, text) for i, (page_no, text) in enumerate(chunks)],
        )


@dataclass
class TopicView:
    item_number: str | None
    title: str
    summary: str | None
    decisions: str | None
    page_no: int | None


@dataclass
class AttendeeView:
    name: str  # as written in the document
    status: str
    role: str | None


@dataclass
class MeetingView:
    title: str
    meeting_type: str
    meeting_date: date | None
    start_time: time | None
    end_time: time | None
    location: str | None
    summary: str | None
    warnings: list[str]
    attendees: list[AttendeeView]
    topics: list[TopicView]

    def names(self, status: str) -> list[str]:
        return [a.name for a in self.attendees if a.status == status]


def load_meeting(conn: psycopg.Connection, rel_path: str) -> MeetingView | None:
    row = conn.execute(
        """
        SELECT m.id, m.title, m.meeting_type, m.meeting_date, m.start_time, m.end_time, m.location,
               m.summary, coalesce(m.raw_extraction -> 'warnings', '[]')
        FROM meetings m JOIN documents d ON d.id = m.document_id
        WHERE d.rel_path = %s
        """,
        (rel_path,),
    ).fetchone()
    if row is None:
        return None
    meeting_id, *fields, warnings = row
    attendees = conn.execute(
        """
        SELECT name_as_written, status, role FROM attendance
        WHERE meeting_id = %s ORDER BY status DESC, name_as_written
        """,
        (meeting_id,),
    ).fetchall()
    topics = conn.execute(
        """
        SELECT item_number, title, summary, decisions, page_no FROM topics
        WHERE meeting_id = %s ORDER BY ordinal
        """,
        (meeting_id,),
    ).fetchall()
    return MeetingView(
        *fields,
        warnings=warnings,
        attendees=[AttendeeView(*a) for a in attendees],
        topics=[TopicView(*t) for t in topics],
    )
