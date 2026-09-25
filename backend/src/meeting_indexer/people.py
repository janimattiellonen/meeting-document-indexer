"""Matching names as written in documents to people, and fixing the matches by hand.

The same person is written many ways: "Antti Esimerkki", "Esimerkki Antti" (older minutes put the
surname first), "Esimerkki, Antti", "Antti Esimerkki," and "Antti (Andy) Esimerkki". name_key maps all of
these to one key, and names with the same key are the same person. Initials ("Antti E.") and first names
alone are ambiguous, so they are never merged automatically: `mi people suggest` lists them, and
`mi people merge` joins them by hand. Merges move the aliases, so they survive re-indexing.
"""

import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher

import psycopg

from meeting_indexer import db
from meeting_indexer.llm import MeetingType
from meeting_indexer.normalize import clean_name
from meeting_indexer.search import lemmas

log = logging.getLogger(__name__)

# pg_trgm similarity above which a new spelling counts as a known person ("Janne Virtanen" ~ "Jane Virtanen").
SIMILARITY_THRESHOLD = 0.8
# Nicknames and notes: "Antti (Andy) Esimerkki", "Tero “Tepi” Testaaja", "Liisa Laine (5§ alk.)".
ASIDES = re.compile(r"\([^)]*\)|[“”\"«»][^“”\"«»]*[“”\"«»]")
# Keys this similar are suggested as the same person, for a typo ("Norrkoski" / "Norkoski").
SUGGEST_RATIO = 0.85


def display_name(name: str) -> str:
    """The name as it should be shown: without asides, and "Esimerkki, Antti" as "Antti Esimerkki"."""
    name = clean_name(ASIDES.sub(" ", name))
    surname, comma, first = name.partition(",")
    if comma and first.strip() and "," not in first:
        name = f"{first.strip()} {surname.strip()}"
    return clean_name(name)


def first_name_first(name: str) -> str:
    """ "Esimerkki Antti" as "Antti Esimerkki": two words, the second a first name and the first not.

    Older minutes put the surname first. Voikko knows common Finnish first names; others stay as written.
    """
    words = name.split()
    if len(words) != 2:
        return name
    try:
        if lemmas.is_first_name(words[1]) and not lemmas.is_first_name(words[0]):
            return f"{words[1]} {words[0]}"
    except lemmas.VoikkoUnavailable:
        pass
    return name


def name_key(name: str) -> str:
    """Spelling-insensitive key: ignores word order, case, punctuation, hyphens and asides."""
    words = re.split(r"[\s,;.]+", ASIDES.sub(" ", name).casefold().replace("-", ""))
    return " ".join(sorted(w for w in words if w))


def is_full_name(key: str) -> bool:
    """A single word ("Antti") is a first name alone: it can't identify one person."""
    return " " in key


def resolve_person(conn: psycopg.Connection, name: str) -> int:
    """Id of the person with this name: a known spelling or key, else a similar one, else a new person."""
    key = name_key(name)
    row = conn.execute(
        "SELECT person_id FROM person_aliases WHERE lower(alias) = lower(%s) LIMIT 1", (name,)
    ).fetchone()
    if row is None and is_full_name(key):
        row = conn.execute(
            """
            SELECT a.person_id FROM person_aliases a
            WHERE a.name_key = %s
            GROUP BY a.person_id
            ORDER BY (SELECT count(*) FROM attendance WHERE person_id = a.person_id) DESC
            LIMIT 1
            """,
            (key,),
        ).fetchone()
    person_id = row[0] if row else _similar_or_new(conn, name)
    conn.execute(
        "INSERT INTO person_aliases (alias, person_id, name_key) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
        (name, person_id, key),
    )
    return person_id


def _similar_or_new(conn: psycopg.Connection, name: str) -> int:
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
        return person_id
    return db.returned_id(
        conn.execute(
            """
            INSERT INTO people (canonical_name) VALUES (%s)
            ON CONFLICT (canonical_name) DO UPDATE SET canonical_name = EXCLUDED.canonical_name
            RETURNING id
            """,
            (first_name_first(display_name(name)) or name,),
        )
    )


class PersonNotFound(LookupError):
    pass


def merge(conn: psycopg.Connection, keep: int, others: list[int]) -> None:
    """Move the attendance and aliases of others to keep, and delete others.

    When both were recorded for the same meeting (the minutes listed two spellings), keep's row stays.
    """
    others = [o for o in dict.fromkeys(others) if o != keep]
    missing = {keep, *others} - {
        row[0] for row in conn.execute("SELECT id FROM people WHERE id = ANY(%s)", ([keep, *others],))
    }
    if missing:
        raise PersonNotFound(", ".join(str(m) for m in sorted(missing)))
    with conn.transaction():
        conn.execute(
            """
            DELETE FROM attendance a WHERE a.person_id = ANY(%(others)s) AND EXISTS (
                SELECT 1 FROM attendance k WHERE k.meeting_id = a.meeting_id AND k.person_id = %(keep)s)
            """,
            {"keep": keep, "others": others},
        )
        # Two of the others can be listed for the same meeting too: keep one row per meeting.
        conn.execute(
            """
            DELETE FROM attendance a WHERE a.person_id = ANY(%(others)s) AND EXISTS (
                SELECT 1 FROM attendance b WHERE b.meeting_id = a.meeting_id
                AND b.person_id = ANY(%(others)s) AND b.person_id < a.person_id)
            """,
            {"others": others},
        )
        conn.execute("UPDATE attendance SET person_id = %s WHERE person_id = ANY(%s)", (keep, others))
        conn.execute("UPDATE person_aliases SET person_id = %s WHERE person_id = ANY(%s)", (keep, others))
        conn.execute("DELETE FROM people WHERE id = ANY(%s)", (others,))
    refresh_names(conn, [keep])


def rename(conn: psycopg.Connection, person_id: int, name: str) -> None:
    """Set the name shown for a person. It is kept from now on, instead of the most common spelling."""
    name = clean_name(name)
    if not name:
        raise ValueError("the name is empty")
    taken = conn.execute(
        "SELECT id FROM people WHERE canonical_name = %s AND id <> %s", (name, person_id)
    ).fetchone()
    if taken:
        raise ValueError(f"person {taken[0]} already has the name {name!r}; merge them instead")
    cur = conn.execute(
        "UPDATE people SET canonical_name = %s, name_fixed = true WHERE id = %s", (name, person_id)
    )
    if cur.rowcount == 0:
        raise PersonNotFound(str(person_id))


def refresh_names(conn: psycopg.Connection, person_ids: list[int] | None = None) -> int:
    """Name each person by their most common spelling in the minutes, first name first, unless renamed
    by hand.

    A full name beats a first name alone however rare it is; ties go to the spelling used most recently.
    Returns how many names changed.
    """
    rows = conn.execute(
        """
        SELECT p.id, p.canonical_name, a.name_as_written
        FROM people p
        JOIN attendance a ON a.person_id = p.id
        JOIN meetings m ON m.id = a.meeting_id
        WHERE NOT p.name_fixed AND (%(ids)s::bigint[] IS NULL OR p.id = ANY(%(ids)s))
        ORDER BY m.meeting_date NULLS FIRST, m.id
        """,
        {"ids": person_ids},
    ).fetchall()
    spellings: dict[int, Counter[str]] = defaultdict(Counter)
    last_seen: dict[tuple[int, str], int] = {}  # position in date order, for ties
    current: dict[int, str] = {}
    for i, (person_id, canonical, written) in enumerate(rows):
        name = first_name_first(display_name(written) or written)
        spellings[person_id][name] += 1
        last_seen[person_id, name] = i
        current[person_id] = canonical
    taken = {name for (name,) in conn.execute("SELECT canonical_name FROM people")}
    changed = 0
    for person_id, counts in spellings.items():
        best = max(counts, key=lambda n: (is_full_name(n), counts[n], last_seen[person_id, n]))
        if best == current[person_id] or best in taken:
            continue
        conn.execute("UPDATE people SET canonical_name = %s WHERE id = %s", (best, person_id))
        taken.discard(current[person_id])
        taken.add(best)
        changed += 1
    return changed


@dataclass
class Merged:
    kept: str
    merged: list[str]


def dedupe(conn: psycopg.Connection) -> list[Merged]:
    """Fill in missing name keys, then merge people who share a full-name key. Returns what was merged."""
    missing = conn.execute("SELECT alias FROM person_aliases WHERE name_key IS NULL").fetchall()
    with conn.cursor() as cur:
        cur.executemany(
            "UPDATE person_aliases SET name_key = %s WHERE alias = %s",
            [(name_key(alias), alias) for (alias,) in missing],
        )
    groups = conn.execute(
        """
        SELECT array_agg(DISTINCT a.person_id) FROM person_aliases a
        WHERE position(' ' IN a.name_key) > 0  -- full names only (is_full_name)
        GROUP BY a.name_key HAVING count(DISTINCT a.person_id) > 1
        """
    ).fetchall()
    # People can be linked through several keys: join the groups that share a person.
    clusters: list[set[int]] = []
    for (ids,) in groups:
        linked = [c for c in clusters if c & set(ids)]
        merged = set(ids).union(*linked)
        clusters = [c for c in clusters if c not in linked] + [merged]

    results: list[Merged] = []
    for cluster in clusters:
        rows = conn.execute(
            """
            SELECT p.id, p.canonical_name FROM people p WHERE p.id = ANY(%s)
            ORDER BY (SELECT count(*) FROM attendance WHERE person_id = p.id) DESC, p.id
            """,
            (list(cluster),),
        ).fetchall()
        (keep, kept_name), *others = rows
        merge(conn, keep, [o for o, _ in others])
        results.append(Merged(kept_name, [name for _, name in others]))
    return results


@dataclass
class PersonRow:
    id: int
    name: str
    meetings: int
    first_year: int | None
    last_year: int | None


def list_people(conn: psycopg.Connection, query: str | None = None) -> list[PersonRow]:
    """People with at least one meeting, by name. query matches any spelling, ignoring case."""
    rows = conn.execute(
        """
        SELECT p.id, p.canonical_name, count(DISTINCT a.meeting_id) FILTER (WHERE a.status = 'present'),
               min(extract(year FROM m.meeting_date))::int, max(extract(year FROM m.meeting_date))::int
        FROM people p
        JOIN attendance a ON a.person_id = p.id
        JOIN meetings m ON m.id = a.meeting_id
        WHERE %(q)s::text IS NULL OR EXISTS (
            SELECT 1 FROM person_aliases pa WHERE pa.person_id = p.id AND pa.alias ILIKE %(pattern)s)
            OR p.canonical_name ILIKE %(pattern)s
        GROUP BY p.id
        ORDER BY lower(p.canonical_name)
        """,
        {"q": query, "pattern": f"%{_escape_like(query or '')}%"},
    ).fetchall()
    return [PersonRow(*row) for row in rows]


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass
class PersonMeeting:
    meeting_id: int
    title: str
    meeting_type: MeetingType
    meeting_date: date | None
    status: str
    role: str | None
    name_as_written: str


@dataclass
class PersonDetail:
    id: int
    name: str
    spellings: list[str]  # every way the minutes write the name
    meetings: list[PersonMeeting]  # newest first


def load_person(conn: psycopg.Connection, person_id: int) -> PersonDetail | None:
    row = conn.execute("SELECT canonical_name FROM people WHERE id = %s", (person_id,)).fetchone()
    if row is None:
        return None
    spellings = [
        alias
        for (alias,) in conn.execute(
            "SELECT alias FROM person_aliases WHERE person_id = %s ORDER BY lower(alias)", (person_id,)
        )
    ]
    meetings = conn.execute(
        """
        SELECT m.id, m.title, m.meeting_type, m.meeting_date, a.status, a.role, a.name_as_written
        FROM attendance a JOIN meetings m ON m.id = a.meeting_id
        WHERE a.person_id = %s
        ORDER BY m.meeting_date DESC NULLS LAST, m.id DESC
        """,
        (person_id,),
    ).fetchall()
    return PersonDetail(person_id, row[0], spellings, [PersonMeeting(*m) for m in meetings])


@dataclass
class Suggestion:
    a: PersonRow
    b: PersonRow
    reason: str


def suggest(conn: psycopg.Connection) -> list[Suggestion]:
    """Pairs of people who may be the same: typos, initials ("Antti E."), and first names alone.

    Every spelling of each person is compared, so "Antti" is suggested for a person named
    "Andy Esimerkki" who is also written "Antti Esimerkki".
    """
    people = list_people(conn)
    spellings: dict[int, set[str]] = defaultdict(set)
    for person_id, alias in conn.execute("SELECT person_id, alias FROM person_aliases"):
        spellings[person_id].add(display_name(alias).casefold())
    for p in people:
        spellings[p.id].add(p.name.casefold())
    suggestions: list[Suggestion] = []
    for i, a in enumerate(people):
        for b in people[i + 1 :]:
            reason = _same_person_reason(spellings[a.id], spellings[b.id])
            if reason:
                suggestions.append(Suggestion(a, b, reason))
    return suggestions


def _same_person_reason(names_a: set[str], names_b: set[str]) -> str | None:
    for a in names_a:
        for b in names_b:
            reason = _pair_reason(a.split(), b.split())
            if reason:
                return reason
    return None


def _pair_reason(words_a: list[str], words_b: list[str]) -> str | None:
    if not words_a or not words_b:
        return None
    if len(words_a) == 1 or len(words_b) == 1:
        single, other = (words_a, words_b) if len(words_a) == 1 else (words_b, words_a)
        if len(other) > 1 and single[0].strip(".") in other:
            return "first name or surname alone"
        return None
    if _initial_matches(words_a, words_b) or _initial_matches(words_b, words_a):
        return "initial"
    if SequenceMatcher(None, " ".join(sorted(words_a)), " ".join(sorted(words_b))).ratio() >= SUGGEST_RATIO:
        return "similar spelling"
    return None


def _initial_matches(short: list[str], full: list[str]) -> bool:
    """ "antti e." or "antti e" against "antti esimerkki" (either word order)."""
    if len(short) != 2 or len(full) != 2:
        return False
    first, initial = short[0], short[1].rstrip(".")
    if len(initial) != 1 or (len(full[0].rstrip(".")) == 1 and len(full[1].rstrip(".")) == 1):
        return False
    return any(f == first and len(s) > 1 and s.startswith(initial) for f, s in (full, full[::-1]))
