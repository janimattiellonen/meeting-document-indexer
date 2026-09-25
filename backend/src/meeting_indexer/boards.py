"""The board of each year, from the attendance of that term's board meetings.

A person listed at a board meeting (present or absent) is on that term's board, unless the minutes mark
them as an outsider ("muut", "ei hallituksessa", "hallituksen ulkopuolinen henkilö"). General meetings
don't count: any member can attend those. The term year comes from normalize.classify_meeting.

This is inferred from attendance, not from who was elected. The minutes of the general meetings that
elect the board would be more authoritative, but the elections are free text; see docs/PLAN.md.
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date

import psycopg

from meeting_indexer import db

# Marks someone attending a board meeting without being on the board.
OUTSIDER = re.compile(
    r"ulkopuol|ei hallitukse|\bmuut?\b|vieras|kutsu|asiantuntija|ilman äänioikeutta", re.IGNORECASE
)
ROLE_NAMES = {
    "pj": "puheenjohtaja",
    "vpj": "varapuheenjohtaja",
    "varapj": "varapuheenjohtaja",
    "siht": "sihteeri",
    "rahastonhoit": "rahastonhoitaja",
}
# Roles in one meeting's procedure, not positions on the board; "jäsen" is every member.
PROCEDURAL = re.compile(
    r"^(?:kokouksen |pöytäkirjan|äänten|äänioikeutettu|jäsen$|hallitus$|hallituksen jäsen$)", re.IGNORECASE
)
# The rest of a role after these is a note about the meeting: "sihteeri kohdat 7-14", "pj (klo 19-20)",
# "liittyi 17.23" (joined at 17.23).
ROLE_NOTE = re.compile(
    r"\s*(?:\(|\bkohd|\bklo\b|\balk|\bliittyi|\bpoistui|\bsaapui|\betä|\d).*$", re.IGNORECASE
)
# Listed first, in this order; other roles follow alphabetically.
ROLE_ORDER = ["puheenjohtaja", "varapuheenjohtaja", "sihteeri", "rahastonhoitaja"]


def board_roles(role: str | None) -> list[str]:
    """Board positions in an attendance role: "Siht., tiedottaja" -> ["sihteeri", "tiedottaja"]."""
    roles: list[str] = []
    for part in re.split(r",|/|\bja\b", role or ""):
        name = ROLE_NOTE.sub("", part.strip()).strip(" .").casefold()
        name = re.sub(r"^(?:hallituksen|yhdistyksen)\s+", "", name)
        name = ROLE_NAMES.get(name, name)
        if name and not PROCEDURAL.match(name) and not OUTSIDER.search(name) and name not in roles:
            roles.append(name)
    return roles


def is_outsider(role: str | None) -> bool:
    return bool(role and OUTSIDER.search(role))


def role_sort_key(role: str) -> tuple[int, str]:
    return (ROLE_ORDER.index(role) if role in ROLE_ORDER else len(ROLE_ORDER), role)


@dataclass
class BoardMeeting:
    id: int
    title: str
    meeting_date: date | None
    meeting_number: str | None


@dataclass
class Role:
    name: str
    meetings: int  # in how many of the year's board meetings the minutes give this role


@dataclass
class BoardMember:
    person_id: int
    name: str
    roles: list[Role]  # most common first
    present: int  # meetings attended
    absent: int  # meetings listed as absent; the rest the minutes don't say


@dataclass
class Board:
    year: int
    meetings: list[BoardMeeting]
    members: list[BoardMember]
    others: list[BoardMember]  # at board meetings, not on the board


@dataclass
class BoardYear:
    year: int
    meetings: int


def board_meetings(conn: psycopg.Connection, year: int | None = None) -> list[tuple[int, BoardMeeting]]:
    """(term year, meeting) for each board meeting, oldest first.

    The same minutes stored twice (as .doc and .pdf) count once (db.SAME_MINUTES).
    """
    rows = conn.execute(
        f"""
        SELECT term_year, id, title, meeting_date, meeting_number FROM (
            SELECT DISTINCT ON (m.term_year, {db.SAME_MINUTES})
                   m.term_year, m.id, m.title, m.meeting_date, m.meeting_number
            FROM meetings m
            WHERE m.meeting_type = 'board' AND m.term_year IS NOT NULL
              AND (%(year)s::int IS NULL OR m.term_year = %(year)s)
            ORDER BY m.term_year, {db.SAME_MINUTES}, m.id
        ) AS distinct_meetings
        ORDER BY meeting_date NULLS LAST, id
        """,
        {"year": year},
    ).fetchall()
    return [(term_year, BoardMeeting(*fields)) for term_year, *fields in rows]


def board_years(conn: psycopg.Connection) -> list[BoardYear]:
    counts = Counter(term_year for term_year, _ in board_meetings(conn))
    return [BoardYear(year, n) for year, n in sorted(counts.items())]


def all_boards(conn: psycopg.Connection, year: int | None = None) -> dict[int, Board]:
    """The board of each year (or only of year), by year. Two queries however many years."""
    meetings_by_year: dict[int, list[BoardMeeting]] = defaultdict(list)
    year_of: dict[int, int] = {}
    for term_year, meeting in board_meetings(conn, year):
        meetings_by_year[term_year].append(meeting)
        year_of[meeting.id] = term_year
    rows = conn.execute(
        """
        SELECT a.meeting_id, a.person_id, p.canonical_name, a.status, a.role
        FROM attendance a JOIN people p ON p.id = a.person_id
        WHERE a.meeting_id = ANY(%s)
        """,
        (list(year_of),),
    ).fetchall()
    rows_by_year: dict[int, list[tuple[int, str, str, str | None]]] = defaultdict(list)
    for meeting_id, person_id, name, status, role in rows:
        rows_by_year[year_of[meeting_id]].append((person_id, name, status, role))
    return {y: _board(y, meetings, rows_by_year[y]) for y, meetings in sorted(meetings_by_year.items())}


def board(conn: psycopg.Connection, year: int) -> Board | None:
    return all_boards(conn, year).get(year)


def _board(year: int, meetings: list[BoardMeeting], rows: list[tuple[int, str, str, str | None]]) -> Board:
    by_person: dict[int, list[tuple[str, str | None]]] = defaultdict(list)
    names: dict[int, str] = {}
    for person_id, name, status, role in rows:
        by_person[person_id].append((status, role))
        names[person_id] = name

    members: list[BoardMember] = []
    others: list[BoardMember] = []
    for person_id, entries in by_person.items():
        role_counts = Counter(r for _, role in entries for r in board_roles(role))
        member = BoardMember(
            person_id=person_id,
            name=names[person_id],
            roles=[
                Role(r, n)
                for r, n in sorted(role_counts.items(), key=lambda rn: (-rn[1], role_sort_key(rn[0])))
            ],
            present=sum(status == "present" for status, _ in entries),
            absent=sum(status == "absent" for status, _ in entries),
        )
        on_board = any(not is_outsider(role) for _, role in entries)
        (members if on_board else others).append(member)

    def order(m: BoardMember) -> tuple:
        if not m.roles:
            return (len(ROLE_ORDER) + 1, "", 0, -m.present, m.name)
        return (*role_sort_key(m.roles[0].name), -m.roles[0].meetings, -m.present, m.name)

    return Board(year, meetings, sorted(members, key=order), sorted(others, key=order))


@dataclass
class BoardTerm:
    year: int
    roles: list[Role]
    present: int
    absent: int
    meetings: int  # board meetings that year


def person_terms(conn: psycopg.Connection, person_id: int) -> list[BoardTerm]:
    """The years a person was on the board, with their roles and attendance."""
    terms: list[BoardTerm] = []
    for year, found in all_boards(conn).items():
        member = next((m for m in found.members if m.person_id == person_id), None)
        if member:
            terms.append(BoardTerm(year, member.roles, member.present, member.absent, len(found.meetings)))
    return terms


def members_by_year(conn: psycopg.Connection) -> dict[int, list[int]]:
    """person id -> the years they were on the board."""
    result: dict[int, list[int]] = defaultdict(list)
    for year, found in all_boards(conn).items():
        for member in found.members:
            result[member.person_id].append(year)
    return result
