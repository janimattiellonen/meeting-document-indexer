import unicodedata

import psycopg
from conftest import MIGRATIONS, up_section

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


def test_nfc_migration_merges_a_path_stored_in_both_forms(conn: psycopg.Connection) -> None:
    # New code registers the NFC form; a database not yet migrated still has the NFD one.
    nfd, nfc = unicodedata.normalize("NFD", "pöytäkirja.pdf"), "pöytäkirja.pdf"
    other = unicodedata.normalize("NFD", "kevätkokous.pdf")
    conn.execute(
        "INSERT INTO documents (rel_path, sha256, file_type, status, updated_at) VALUES"
        " (%s, 'x', 'pdf', 'indexed', now() - interval '1 day'), (%s, NULL, 'pdf', 'pending', now()),"
        " (%s, 'y', 'pdf', 'failed', now())",
        (nfd, nfc, other),
    )

    conn.execute(up_section(MIGRATIONS / "20260923160000_nfc_document_paths.sql").encode())

    assert db.stored_hashes(conn) == {nfc: ("x", "indexed"), "kevätkokous.pdf": ("y", "failed")}
