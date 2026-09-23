"""Integration tests against the compose database (`docker compose up -d`). Skipped if it isn't running."""

import psycopg
import pytest

from meeting_indexer import db


@pytest.fixture
def conn():
    try:
        connection = db.connect()
    except psycopg.OperationalError:
        pytest.skip("database not running")
    with connection:
        yield connection
        connection.rollback()


def test_schema_is_migrated(conn: psycopg.Connection) -> None:
    tables = {row[0] for row in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
    assert {"documents", "meetings", "topics", "chunks", "people", "attendance"} <= tables


def test_finnish_stemming_matches_inflected_forms(conn: psycopg.Connection) -> None:
    # The Snowball stemmer misses stem changes (hallitus/hallituksen, kisa/kisoille); see docs/PLAN.md §8.
    row = conn.execute(
        "SELECT to_tsvector('finnish', 'Keskusteltiin verkkosivujen uudistuksesta')"
        " @@ websearch_to_tsquery('finnish', 'verkkosivut')"
    ).fetchone()
    assert row == (True,)


def test_document_counts_groups_by_status(conn: psycopg.Connection) -> None:
    conn.execute(
        "INSERT INTO documents (rel_path, sha256, file_type, status)"
        " VALUES ('test/a.pdf', 'x', 'pdf', 'indexed'), ('test/b.pdf', 'y', 'pdf', 'failed')"
    )
    counts = db.document_counts(conn)
    assert counts.get("indexed", 0) >= 1
    assert counts.get("failed", 0) >= 1
