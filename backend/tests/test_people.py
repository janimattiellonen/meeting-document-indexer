"""Matching names to people, and fixing matches by hand. All names are invented."""

import psycopg
import pytest

from meeting_indexer import people
from meeting_indexer.people import display_name, first_name_first, name_key


@pytest.mark.parametrize(
    "spelling",
    [
        "Antti Esimerkki",
        "Esimerkki Antti",
        "Esimerkki, Antti",
        "Antti Esimerkki,",
        "antti ESIMERKKI",
        "Antti (Andy) Esimerkki",
        "Antti “Ande” Esimerkki",
        "Antti Esimerkki (5§ alk.)",
    ],
)
def test_spellings_of_one_name_share_a_key(spelling: str) -> None:
    assert name_key(spelling) == name_key("Antti Esimerkki")


def test_hyphens_do_not_change_the_key() -> None:
    assert name_key("Matti Keski-Esimerkki") == name_key("Matti Keskiesimerkki")
    assert name_key("J-P Esimerkki") == name_key("JP Esimerkki")


def test_different_names_have_different_keys() -> None:
    assert name_key("Mikko Esimerkki") != name_key("Mika Esimerkki")
    assert name_key("Antti E.") != name_key("Antti Esimerkki")


def test_display_name_drops_asides_and_puts_a_comma_name_in_order() -> None:
    assert display_name("Esimerkki, Antti") == "Antti Esimerkki"
    assert display_name("Antti (Andy) Esimerkki") == "Antti Esimerkki"
    assert display_name("Antti Esimerkki,") == "Antti Esimerkki"


def test_surname_first_is_turned_around_when_voikko_knows_the_first_name() -> None:
    assert first_name_first("Virtanen Liisa") == "Liisa Virtanen"
    assert first_name_first("Liisa Virtanen") == "Liisa Virtanen"
    # Unknown words stay as written: without knowing which is the first name, nothing is guessed.
    assert first_name_first("Qwertix Kvarnbäck") == "Qwertix Kvarnbäck"


# Against the database


def add_meeting(conn: psycopg.Connection, day: str, attendees: list[tuple[str, str]]) -> int:
    document_id = conn.execute(
        "INSERT INTO documents (rel_path, file_type, status) VALUES (%s, 'pdf', 'indexed') RETURNING id",
        (f"{day}.pdf",),
    ).fetchone()[0]  # type: ignore[index]
    meeting_id = conn.execute(
        """
        INSERT INTO meetings (document_id, title, meeting_type, meeting_date, raw_extraction)
        VALUES (%s, 'Hallituksen kokous', 'board', %s, '{}') RETURNING id
        """,
        (document_id, day),
    ).fetchone()[0]  # type: ignore[index]
    for name, status in attendees:
        person_id = people.resolve_person(conn, name)
        conn.execute(
            "INSERT INTO attendance (meeting_id, person_id, status, name_as_written) VALUES (%s, %s, %s, %s)",
            (meeting_id, person_id, status, name),
        )
    return meeting_id


def person_names(conn: psycopg.Connection) -> list[str]:
    return [name for (name,) in conn.execute("SELECT canonical_name FROM people ORDER BY canonical_name")]


def test_surname_first_and_comma_spellings_resolve_to_the_same_person(conn: psycopg.Connection) -> None:
    first = people.resolve_person(conn, "Qwertix Kvarnbäck")
    assert people.resolve_person(conn, "Kvarnbäck Qwertix") == first
    assert people.resolve_person(conn, "Kvarnbäck, Qwertix") == first


def test_first_name_alone_is_not_matched_by_key(conn: psycopg.Connection) -> None:
    full = people.resolve_person(conn, "Qwertix Kvarnbäck")
    assert people.resolve_person(conn, "Qwertix") != full


def test_new_person_is_named_first_name_first(conn: psycopg.Connection) -> None:
    people.resolve_person(conn, "Virtanen, Liisa")
    assert person_names(conn) == ["Liisa Virtanen"]


def test_merge_moves_meetings_and_spellings(conn: psycopg.Connection) -> None:
    add_meeting(conn, "2023-01-10", [("Liisa Virtanen", "present")])
    add_meeting(conn, "2023-02-10", [("Liisa", "present")])
    full = people.resolve_person(conn, "Liisa Virtanen")
    alone = people.resolve_person(conn, "Liisa")

    people.merge(conn, full, [alone])

    assert person_names(conn) == ["Liisa Virtanen"]
    assert conn.execute("SELECT count(*) FROM attendance WHERE person_id = %s", (full,)).fetchone() == (2,)
    # A later document writing the name alone finds the merged person.
    assert people.resolve_person(conn, "Liisa") == full


def test_merge_keeps_one_row_when_both_were_listed_for_the_same_meeting(conn: psycopg.Connection) -> None:
    add_meeting(conn, "2023-01-10", [("Liisa Virtanen", "present"), ("Liisa", "present")])
    full, alone = people.resolve_person(conn, "Liisa Virtanen"), people.resolve_person(conn, "Liisa")

    people.merge(conn, full, [alone])

    assert conn.execute("SELECT count(*) FROM attendance").fetchone() == (1,)


def test_merging_an_unknown_person_changes_nothing(conn: psycopg.Connection) -> None:
    full = people.resolve_person(conn, "Liisa Virtanen")
    with pytest.raises(people.PersonNotFound):
        people.merge(conn, full, [999])
    assert person_names(conn) == ["Liisa Virtanen"]


def test_names_follow_the_most_common_spelling_unless_renamed(conn: psycopg.Connection) -> None:
    add_meeting(conn, "2020-01-10", [("Virtanen Liisa", "present")])
    add_meeting(conn, "2023-01-10", [("Liisa Virtanen", "present")])
    add_meeting(conn, "2024-01-10", [("Liisa Virtanen", "present")])
    person_id = people.resolve_person(conn, "Liisa Virtanen")
    conn.execute("UPDATE people SET canonical_name = 'Virtanen Liisa' WHERE id = %s", (person_id,))

    assert people.refresh_names(conn) == 1
    assert person_names(conn) == ["Liisa Virtanen"]

    people.rename(conn, person_id, "Liisa Maria Virtanen")
    people.refresh_names(conn)
    assert person_names(conn) == ["Liisa Maria Virtanen"]


def test_rename_refuses_another_persons_name(conn: psycopg.Connection) -> None:
    people.resolve_person(conn, "Liisa Virtanen")
    other = people.resolve_person(conn, "Teppo Testaaja")
    with pytest.raises(ValueError, match="merge them instead"):
        people.rename(conn, other, "Liisa Virtanen")


def add_stored_person(conn: psycopg.Connection, name: str) -> int:
    """A person stored before name keys existed: no key, and not matched to anyone."""
    person_id = conn.execute(
        "INSERT INTO people (canonical_name) VALUES (%s) RETURNING id", (name,)
    ).fetchone()[0]  # type: ignore[index]
    conn.execute("INSERT INTO person_aliases (alias, person_id) VALUES (%s, %s)", (name, person_id))
    return person_id


def test_dedupe_merges_people_whose_spellings_share_a_key(conn: psycopg.Connection) -> None:
    add_stored_person(conn, "Kvarnbäck Qwertix")
    add_stored_person(conn, "Qwertix Kvarnbäck")
    add_stored_person(conn, "Qwertix")
    add_meeting(conn, "2012-01-10", [("Kvarnbäck Qwertix", "present")])
    add_meeting(conn, "2023-01-10", [("Qwertix Kvarnbäck", "present"), ("Qwertix", "present")])

    merged = people.dedupe(conn)

    assert len(merged) == 1
    # The first name alone stays apart: it could be anyone called Qwertix.
    assert len(person_names(conn)) == 2
    assert conn.execute("SELECT count(*) FROM person_aliases WHERE name_key IS NULL").fetchone() == (0,)


def test_suggestions_include_initials_first_names_and_typos(conn: psycopg.Connection) -> None:
    add_meeting(
        conn,
        "2015-01-10",
        [
            ("Liisa Virtanen", "present"),
            ("Liisa V.", "present"),
            ("Teppo Testaaja", "present"),
            ("Teppo", "present"),
            ("Maija Meikäläinen", "present"),
            ("Maija Meikälainen", "present"),
            ("Kalle Kokeilija", "present"),
        ],
    )
    reasons = {frozenset((s.a.name, s.b.name)): s.reason for s in people.suggest(conn)}

    assert reasons[frozenset(("Liisa Virtanen", "Liisa V."))] == "initial"
    assert reasons[frozenset(("Teppo Testaaja", "Teppo"))] == "first name or surname alone"
    assert reasons[frozenset(("Maija Meikäläinen", "Maija Meikälainen"))] == "similar spelling"
    assert not any("Kalle Kokeilija" in pair for pair in reasons)


def test_list_people_matches_any_spelling(conn: psycopg.Connection) -> None:
    add_meeting(conn, "2012-01-10", [("Kvarnbäck, Qwertix", "present"), ("Liisa Virtanen", "absent")])
    found = people.list_people(conn, "kvarnbäck, q")
    assert [(p.name, p.meetings, p.first_year) for p in found] == [("Qwertix Kvarnbäck", 1, 2012)]
    assert people.list_people(conn, "100%") == []
