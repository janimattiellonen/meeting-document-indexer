"""The indexing pipeline against a real test database, with the LLM replaced by a fake."""

from datetime import date
from pathlib import Path

import psycopg
import pymupdf
import pytest

from meeting_indexer import db
from meeting_indexer.indexer import index_file, index_paths
from meeting_indexer.llm import Meeting, Person, Topic

MODEL = "fake-model"
PAGE_1 = """Hallituksen kokous 3/2019
Aika: 2.4.2019 klo 18.00
Paikka: Seuratalo
Läsnä: Maija Meikäläinen (pj), Teppo Testaaja (siht.)
Poissa: Liisa Laine
1. Kokouksen avaus
2. Seuran verkkosivut"""
PAGE_2 = "Päätettiin uudistaa verkkosivut.\n3. Kokouksen päättäminen"


def fake_meeting(**overrides) -> Meeting:
    fields = {
        "title": "Hallituksen kokous 3/2019",
        "meeting_type": "board",
        "date": "2019-04-02",
        "start_time": "18:00",
        "end_time": None,
        "location": "Seuratalo",
        "present": [Person(name="Maija Meikäläinen", role="Puheenjohtaja"), Person(name="Teppo Testaaja")],
        "absent": [Person(name="Liisa Laine")],
        "topics": [
            Topic(title="1. Kokouksen avaus", summary="Avattiin."),
            Topic(
                title="2. Seuran verkkosivut",
                summary="Keskusteltiin verkkosivuista.",
                decisions="Uudistetaan verkkosivut.",
            ),
            Topic(title="3. Kokouksen päättäminen", summary="Päätettiin."),
        ],
        "summary": "Hallitus päätti uudistaa verkkosivut.",
    }
    return Meeting(**(fields | overrides))


class FakeExtractor:
    def __init__(self, meeting: Meeting | None = None, error: Exception | None = None) -> None:
        self.meeting = meeting or fake_meeting()
        self.error = error
        self.calls = 0

    def __call__(self, pages: list[str]) -> Meeting:
        self.calls += 1
        if self.error:
            raise self.error
        return self.meeting


def write_pdf(path: Path, pages: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = pymupdf.open()
    for text in pages:
        page = pdf.new_page()
        if text:
            page.insert_text((72, 72), text, fontname="helv")
    pdf.save(path)
    return path


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "documents"


@pytest.fixture
def minutes(root: Path) -> Path:
    return write_pdf(root / "2019" / "hallitus-3-2019.pdf", [PAGE_1, PAGE_2])


def test_document_is_stored_with_meeting_people_topics_and_chunks(
    conn: psycopg.Connection, root: Path, minutes: Path
) -> None:
    result = index_file(conn, minutes, root, FakeExtractor(), MODEL)

    assert result.status == "indexed"
    assert result.warnings == []
    meeting = db.load_meeting(conn, "2019/hallitus-3-2019.pdf")
    assert meeting is not None
    assert meeting.meeting_date == date(2019, 4, 2)
    assert meeting.location == "Seuratalo"
    assert meeting.names("present") == ["Maija Meikäläinen", "Teppo Testaaja"]
    assert meeting.names("absent") == ["Liisa Laine"]
    assert meeting.attendees[0].role == "puheenjohtaja"
    assert [(t.item_number, t.title, t.page_no) for t in meeting.topics] == [
        ("1", "Kokouksen avaus", 1),
        ("2", "Seuran verkkosivut", 1),
        ("3", "Kokouksen päättäminen", 2),
    ]
    assert meeting.topics[1].decisions == "Uudistetaan verkkosivut."
    chunk_pages = conn.execute("SELECT page_no FROM chunks ORDER BY ordinal").fetchall()
    assert chunk_pages == [(1,), (2,)]


def test_stored_topics_are_found_by_finnish_full_text_search(
    conn: psycopg.Connection, root: Path, minutes: Path
) -> None:
    index_file(conn, minutes, root, FakeExtractor(), MODEL)
    rows = conn.execute(
        "SELECT title FROM topics WHERE search_tsv @@ websearch_to_tsquery('finnish', 'verkkosivujen')"
    ).fetchall()
    assert rows == [("Seuran verkkosivut",)]


def test_each_document_is_committed_on_its_own(
    conn: psycopg.Connection, database_url: str, root: Path, minutes: Path
) -> None:
    index_file(conn, minutes, root, FakeExtractor(), MODEL)
    with psycopg.connect(database_url) as other:  # a separate session sees it immediately
        assert other.execute("SELECT count(*) FROM meetings").fetchone() == (1,)


def test_unchanged_document_is_skipped(conn: psycopg.Connection, root: Path, minutes: Path) -> None:
    extractor = FakeExtractor()
    index_file(conn, minutes, root, extractor, MODEL)

    assert index_file(conn, minutes, root, extractor, MODEL).status == "skipped"
    assert extractor.calls == 1


@pytest.mark.parametrize("change", ["file", "model", "force"])
def test_document_is_reindexed_when_something_changed(
    conn: psycopg.Connection, root: Path, minutes: Path, change: str
) -> None:
    extractor = FakeExtractor()
    index_file(conn, minutes, root, extractor, MODEL)
    if change == "file":
        write_pdf(minutes, [PAGE_1, PAGE_2 + "\nLisäys."])

    result = index_file(
        conn, minutes, root, extractor, "other-model" if change == "model" else MODEL, force=change == "force"
    )

    assert result.status == "indexed"
    assert extractor.calls == 2
    assert conn.execute("SELECT count(*) FROM meetings").fetchone() == (1,)
    assert conn.execute("SELECT count(*) FROM topics").fetchone() == (3,)


def test_scanned_document_is_recorded_without_calling_the_llm(conn: psycopg.Connection, root: Path) -> None:
    scan = write_pdf(root / "scan.pdf", ["", ""])
    extractor = FakeExtractor()

    assert index_file(conn, scan, root, extractor, MODEL).status == "no_text"
    assert extractor.calls == 0
    assert db.documents_with_status(conn, "no_text") == [("scan.pdf", None)]
    assert index_file(conn, scan, root, extractor, MODEL).status == "skipped"


def test_failure_is_recorded_and_keeps_the_earlier_extraction(
    conn: psycopg.Connection, root: Path, minutes: Path
) -> None:
    index_file(conn, minutes, root, FakeExtractor(), MODEL)

    result = index_file(
        conn, minutes, root, FakeExtractor(error=ConnectionError("ollama down")), MODEL, force=True
    )

    assert result.status == "failed"
    assert db.documents_with_status(conn, "failed") == [
        ("2019/hallitus-3-2019.pdf", "ConnectionError: ollama down")
    ]
    assert db.load_meeting(conn, "2019/hallitus-3-2019.pdf") is not None
    # A failed document is retried on the next run even though the file hasn't changed.
    assert index_file(conn, minutes, root, FakeExtractor(), MODEL).status == "indexed"


def test_one_failing_document_does_not_stop_the_run(
    conn: psycopg.Connection, root: Path, minutes: Path
) -> None:
    broken = root / "broken.pdf"
    broken.write_bytes(b"not a pdf")

    results = index_paths(conn, [broken, minutes], root, FakeExtractor(), MODEL)

    assert [r.status for r in results] == ["failed", "indexed"]


def test_invalid_model_date_falls_back_to_the_text_with_a_warning(
    conn: psycopg.Connection, root: Path, minutes: Path
) -> None:
    result = index_file(conn, minutes, root, FakeExtractor(fake_meeting(date="2.4.2019")), MODEL)

    assert any("date" in w for w in result.warnings)
    meeting = db.load_meeting(conn, "2019/hallitus-3-2019.pdf")
    assert meeting is not None
    assert meeting.meeting_date == date(2019, 4, 2)
    assert meeting.warnings == result.warnings


def test_same_person_is_recognised_across_meetings_and_spellings(
    conn: psycopg.Connection, root: Path
) -> None:
    first = write_pdf(root / "a.pdf", [PAGE_1, PAGE_2])
    second = write_pdf(root / "b.pdf", [PAGE_1.replace("3/2019", "4/2019"), PAGE_2])
    known = [Person(name="Janne Virtanen"), Person(name="Mikko Virtanen"), Person(name="Maija Meikäläinen")]
    index_file(conn, first, root, FakeExtractor(fake_meeting(present=known, absent=[])), MODEL)
    variants = [Person(name="Jane Virtanen"), Person(name="Mika Virtanen"), Person(name="maija meikäläinen")]

    index_file(conn, second, root, FakeExtractor(fake_meeting(present=variants, absent=[])), MODEL)

    people = conn.execute("SELECT canonical_name FROM people ORDER BY canonical_name").fetchall()
    # "Jane" is a close misspelling of "Janne" (similarity 0.87) and case differences are ignored,
    # but "Mika" and "Mikko" (0.71) are different people and must stay apart.
    assert people == [("Janne Virtanen",), ("Maija Meikäläinen",), ("Mika Virtanen",), ("Mikko Virtanen",)]
    aliases = {row[0] for row in conn.execute("SELECT alias FROM person_aliases")}
    assert "Jane Virtanen" in aliases


def test_person_listed_twice_gives_a_warning(conn: psycopg.Connection, root: Path, minutes: Path) -> None:
    twice = fake_meeting(present=[Person(name="Teppo Testaaja")], absent=[Person(name="Teppo Testaaja")])

    result = index_file(conn, minutes, root, FakeExtractor(twice), MODEL)

    assert result.status == "indexed"
    assert any("more than once" in w for w in result.warnings)
