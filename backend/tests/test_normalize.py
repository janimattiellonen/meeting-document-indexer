from datetime import date, time

import pytest

from meeting_indexer.normalize import (
    Warnings,
    chunk_pages,
    clean_name,
    clean_role,
    clean_title,
    meeting_date,
    parse_time,
    split_item_number,
    topic_pages,
)

TODAY = date(2026, 9, 23)


def test_valid_model_date_is_used() -> None:
    warnings = Warnings()
    assert meeting_date("2019-04-02", "Aika: 1.1.2018", warnings, TODAY) == date(2019, 4, 2)
    assert warnings.items == []


@pytest.mark.parametrize("value", [None, "", "2.4.2019", "2019-13-01", "1919-04-02", "2031-01-01"])
def test_invalid_or_implausible_model_date_falls_back_to_the_text(value: str | None) -> None:
    warnings = Warnings()
    first_page = "Hallituksen kokous 2/2019\nAika: 2.4.2019 klo 18.00"
    assert meeting_date(value, first_page, warnings, TODAY) == date(2019, 4, 2)
    assert len(warnings.items) == 1


def test_fallback_skips_impossible_dates_in_the_text() -> None:
    assert meeting_date(None, "31.2.2019 ja 5.3.2019", Warnings(), TODAY) == date(2019, 3, 5)


def test_no_date_anywhere_gives_none() -> None:
    assert meeting_date(None, "ei päivämäärää", Warnings(), TODAY) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [("18:05", time(18, 5)), ("18.05", time(18, 5)), ("klo 18", time(18, 0)), ("7:30", time(7, 30))],
)
def test_parse_time(value: str, expected: time) -> None:
    assert parse_time(value) == expected


@pytest.mark.parametrize("value", [None, "", "25:00", "18:75", "illalla"])
def test_parse_time_rejects_nonsense(value: str | None) -> None:
    assert parse_time(value) is None


PAGES = [
    "Hallituksen kokous\n1. Kokouksen avaus\nAvattiin.\n2. Esityslistan hyväksyminen\nHyväksyttiin.",
    "3. Seuran   verkkosivut\nKeskusteltiin.\n4. Muut asiat\nEi muita asioita.",
]


def test_topic_pages_found_by_exact_title_ignoring_case_and_spacing() -> None:
    warnings = Warnings()
    titles = ["Kokouksen avaus", "ESITYSLISTAN HYVÄKSYMINEN", "Seuran verkkosivut", "Muut asiat"]
    assert topic_pages(titles, PAGES, warnings) == [1, 1, 2, 2]
    assert warnings.items == []


def test_topic_pages_tolerates_small_differences_from_the_model() -> None:
    assert topic_pages(["Seuran verkkosivu"], PAGES, Warnings()) == [2]


def test_topic_not_in_text_gets_no_page_and_a_warning() -> None:
    warnings = Warnings()
    assert topic_pages(["Jäsenmaksun korotus"], PAGES, warnings) == [None]
    assert "Jäsenmaksun korotus" in warnings.items[0]


def test_topic_pages_do_not_go_backwards() -> None:
    # "Muut asiat" is also mentioned on page 1, but as the topic after one on page 2 it must be on page 2.
    pages = ["1. Avaus\nMuut asiat käsitellään lopuksi.", "2. Talous\n3. Muut asiat"]
    assert topic_pages(["Avaus", "Talous", "Muut asiat"], pages, Warnings()) == [1, 2, 2]


def test_chunks_keep_page_numbers_and_size_limit() -> None:
    pages = ["\n".join(f"rivi {i}" for i in range(100)), "toinen sivu"]
    chunks = chunk_pages(pages, max_chars=200)
    assert {page for page, _ in chunks} == {1, 2}
    assert all(len(text) <= 200 for _, text in chunks)
    assert "\n".join(text for page, text in chunks if page == 1).split("\n") == pages[0].split("\n")
    assert chunks[-1] == (2, "toinen sivu")


def test_chunks_skip_empty_pages() -> None:
    assert chunk_pages(["", "  \n ", "teksti"]) == [(3, "teksti")]


def test_clean_name_and_role() -> None:
    assert clean_name("  Maija   Meikäläinen\n") == "Maija Meikäläinen"
    assert clean_role(" Puheenjohtaja ") == "puheenjohtaja"
    assert clean_role("  ") is None
    assert clean_role(None) is None


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("5. Sihteerin kone", ("5", "Sihteerin kone")),
        ("12a) Muut asiat", ("12a", "Muut asiat")),
        ("Sihteerin kone", (None, "Sihteerin kone")),
        ("20 vuotta täynnä", (None, "20 vuotta täynnä")),
        ("2026 budjetti", (None, "2026 budjetti")),
    ],
)
def test_split_item_number(title: str, expected: tuple[str | None, str]) -> None:
    assert split_item_number(title) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("ESIMERKKISEURA RY Hallituksen kokous 4/2026", "Hallituksen kokous 4/2026"),
        ("Esimerkkiseura ry  Syyskokous 2019", "Syyskokous 2019"),
        ("Esimerkkiseura ry:n hallituksen kokous", "Esimerkkiseura ry:n hallituksen kokous"),
        ("Hallituksen kokous 4/2026", "Hallituksen kokous 4/2026"),
    ],
)
def test_clean_title_removes_the_letterhead(title: str, expected: str) -> None:
    assert clean_title(title) == expected
