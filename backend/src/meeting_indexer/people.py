"""Matching names as written in documents to people."""

import logging

import psycopg

from meeting_indexer import db

log = logging.getLogger(__name__)

# pg_trgm similarity above which a new spelling counts as a known person ("Janne Virtanen" ~ "Jane Virtanen").
SIMILARITY_THRESHOLD = 0.8


def resolve_person(conn: psycopg.Connection, name: str) -> int:
    """Id of the person with this name: a known spelling, else a similar one, else a new person."""
    row = conn.execute(
        "SELECT person_id FROM person_aliases WHERE lower(alias) = lower(%s) LIMIT 1", (name,)
    ).fetchone()
    if row:
        return row[0]

    row = conn.execute(
        """
        SELECT a.person_id, p.canonical_name, similarity(a.alias, %(name)s) AS score
        FROM person_aliases a JOIN people p ON p.id = a.person_id
        WHERE similarity(a.alias, %(name)s) >= %(threshold)s
        ORDER BY score DESC
        LIMIT 1
        """,
        {"name": name, "threshold": SIMILARITY_THRESHOLD},
    ).fetchone()
    if row:
        person_id, canonical, score = row
        log.info("matched %r to %r (similarity %.2f)", name, canonical, score)
    else:
        person_id = db.returned_id(
            conn.execute(
                """
                INSERT INTO people (canonical_name) VALUES (%s)
                ON CONFLICT (canonical_name) DO UPDATE SET canonical_name = EXCLUDED.canonical_name
                RETURNING id
                """,
                (name,),
            )
        )

    conn.execute(
        "INSERT INTO person_aliases (alias, person_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (name, person_id),
    )
    return person_id
