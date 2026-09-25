"""The board of a year, from board meeting attendance. Documents go through the real pipeline with the
LLM replaced by a fake. All names and content are invented."""

from pathlib import Path

import psycopg
import pytest
from helpers import MODEL, FakeAnalyzer, fake_meeting, write_pdf

from meeting_indexer import boards
from meeting_indexer.boards import board_roles
from meeting_indexer.indexer import index_file
from meeting_indexer.llm import Person


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        ("Puheenjohtaja", ["puheenjohtaja"]),
        ("pj", ["puheenjohtaja"]),
        ("Siht., tiedottaja", ["sihteeri", "tiedottaja"]),
        ("varapj, sihteeri", ["varapuheenjohtaja", "sihteeri"]),
        ("hallituksen varapuheenjohtaja, kokouksen puheenjohtaja", ["varapuheenjohtaja"]),
        ("sihteeri kohdat 7-14", ["sihteeri"]),
        ("sihteeri (klo 19-20)", ["sihteeri"]),
        ("puheenjohtaja, liittyi 17.23", ["puheenjohtaja"]),
        ("pöytäkirjantarkastaja, ääntenlaskija", []),
        ("hallituksen jäsen", []),
        ("5§ alk.", []),
        (None, []),
    ],
)
def test_board_roles_keep_positions_and_drop_meeting_procedure(role: str | None, expected: list[str]) -> None:
    assert board_roles(role) == expected


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "documents"


def add(
    conn: psycopg.Connection,
    root: Path,
    name: str,
    header: str,
    day: str,
    present: list[Person],
    absent: list[Person] | None = None,
    model_type: str = "autumn_general",  # deliberately wrong, as the model often was: the header decides
) -> None:
    path = write_pdf(root / f"{name}.pdf", [f"{header}\nAika: {day}\n1. Kokouksen avaus"])
    meeting = fake_meeting(
        title=header,
        meeting_type=model_type,
        date=day,
        present=present,
        absent=absent or [],
    )
    index_file(conn, path, root, FakeAnalyzer(meeting), MODEL)


def P(name: str, role: str | None = None) -> Person:
    return Person(name=name, role=role)


@pytest.fixture
def year_2023(conn: psycopg.Connection, root: Path) -> None:
    # The organizing meeting, held in December 2022 for the 2023 board.
    add(
        conn,
        root,
        "jarj",
        "Hallituksen järjestäytymiskokous",
        "2022-12-15",
        [P("Maija Meikäläinen", "puheenjohtaja"), P("Teppo Testaaja", "sihteeri"), P("Liisa Laine")],
    )
    add(
        conn,
        root,
        "h1",
        "Hallituksen kokous 1/2023",
        "2023-01-20",
        [
            P("Maija Meikäläinen", "pj"),
            P("Teppo Testaaja", "siht."),
            P("Kalle Kokeilija", "ei hallituksessa"),
        ],
        [P("Liisa Laine")],
    )
    # The same minutes stored twice (.doc and .pdf) count once.
    add(
        conn,
        root,
        "h2",
        "Hallituksen kokous 2/2023",
        "2023-03-01",
        [P("Maija Meikäläinen", "puheenjohtaja"), P("Liisa Laine", "sihteeri")],
    )
    add(
        conn,
        root,
        "h2-copy",
        "Hallituksen kokous 2/2023",
        "2023-03-01",
        [P("Maija Meikäläinen", "puheenjohtaja"), P("Liisa Laine", "sihteeri")],
    )
    # A general meeting: anyone can attend, so it says nothing about the board.
    add(
        conn,
        root,
        "syys",
        "Yhdistyksen syyskokous",
        "2023-11-01",
        [P("Maija Meikäläinen"), P("Olli Osallistuja")],
        model_type="board",
    )


def test_board_is_the_people_at_the_years_board_meetings(conn: psycopg.Connection, year_2023: None) -> None:
    board = boards.board(conn, 2023)

    assert board is not None
    assert [m.meeting_number for m in board.meetings] == [None, "1/2023", "2/2023"]
    members = {m.name: m for m in board.members}
    assert list(members) == ["Maija Meikäläinen", "Teppo Testaaja", "Liisa Laine"]
    maija = members["Maija Meikäläinen"]
    assert [(r.name, r.meetings) for r in maija.roles] == [("puheenjohtaja", 3)]
    assert (maija.present, maija.absent) == (3, 0)
    liisa = members["Liisa Laine"]
    assert (liisa.present, liisa.absent) == (2, 1)
    assert [(r.name, r.meetings) for r in liisa.roles] == [("sihteeri", 1)]
    assert [m.name for m in board.others] == ["Kalle Kokeilija"]


def test_general_meetings_and_other_years_are_not_counted(conn: psycopg.Connection, year_2023: None) -> None:
    assert boards.board(conn, 2022) is None
    assert [(y.year, y.meetings) for y in boards.board_years(conn)] == [(2023, 3)]
    board = boards.board(conn, 2023)
    assert board is not None
    assert "Olli Osallistuja" not in {m.name for m in board.members + board.others}


def person_id(conn: psycopg.Connection, name: str) -> int:
    row = conn.execute("SELECT id FROM people WHERE canonical_name = %s", (name,)).fetchone()
    assert row is not None
    return row[0]


def test_person_terms_and_members_by_year(conn: psycopg.Connection, year_2023: None) -> None:
    liisa, kalle = person_id(conn, "Liisa Laine"), person_id(conn, "Kalle Kokeilija")

    (term,) = boards.person_terms(conn, liisa)
    assert (term.year, term.present, term.absent, term.meetings) == (2023, 2, 1, 3)
    assert boards.person_terms(conn, kalle) == []
    by_person = boards.members_by_year(conn)
    assert by_person[liisa] == [2023]
    assert kalle not in by_person
