"""Database access."""

import psycopg

from meeting_indexer.config import get_settings


def connect() -> psycopg.Connection:
    return psycopg.connect(get_settings().database_url, connect_timeout=5)


def document_counts(conn: psycopg.Connection) -> dict[str, int]:
    """Number of documents per indexing status."""
    rows = conn.execute("SELECT status, count(*) FROM documents GROUP BY status").fetchall()
    return {status: count for status, count in rows}
