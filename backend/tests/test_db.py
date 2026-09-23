import psycopg

from meeting_indexer import db


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
        " VALUES ('a.pdf', 'x', 'pdf', 'indexed'), ('b.pdf', 'y', 'pdf', 'indexed'),"
        " ('c.pdf', 'z', 'pdf', 'failed')"
    )
    assert db.document_counts(conn) == {"indexed": 2, "failed": 1}
    assert [(d.rel_path, d.status) for d in db.problem_documents(conn)] == [("c.pdf", "failed")]


def test_register_pending_adds_only_new_files(conn: psycopg.Connection) -> None:
    assert db.register_pending(conn, [("a.pdf", "pdf"), ("b.doc", "doc")]) == 2
    assert db.register_pending(conn, [("a.pdf", "pdf"), ("c.pdf", "pdf")]) == 1
    assert db.stored_hashes(conn) == {
        "a.pdf": (None, "pending"),
        "b.doc": (None, "pending"),
        "c.pdf": (None, "pending"),
    }
