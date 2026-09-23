"""The indexing pipeline against a real test database, with the LLM replaced by a fake."""

import time
import unicodedata
from datetime import date
from pathlib import Path

import psycopg
import pytest
from helpers import MODEL, PAGE_1, PAGE_2, FakeAnalyzer, fake_meeting, write_pdf

from meeting_indexer import db
from meeting_indexer.indexer import Analysis, RunStopped, index_file, index_paths
from meeting_indexer.limits import run_with_time_limit
from meeting_indexer.llm import Person


class FailingFor(FakeAnalyzer):
    """Fails for the named files, succeeds for the rest."""

    def __init__(self, *names: str) -> None:
        super().__init__()
        self.names = names

    def __call__(self, path: Path) -> Analysis:
        if path.name in self.names:
            raise RuntimeError(f"cannot read {path.name}")
        return super().__call__(path)


def hanging_analyzer(path: Path) -> Analysis:
    """Stands in for a document that hangs: real worker process, real time limit."""
    return run_with_time_limit(time.sleep, 60, seconds=1)


def status_of(conn: psycopg.Connection, rel_path: str) -> tuple:
    return (
        conn.execute(
            "SELECT status, attempts, error IS NOT NULL FROM documents WHERE rel_path = %s", (rel_path,)
        ).fetchone()
        or ()
    )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "documents"


@pytest.fixture
def minutes(root: Path) -> Path:
    return write_pdf(root / "2019" / "hallitus-3-2019.pdf", [PAGE_1, PAGE_2])


@pytest.fixture
def many(root: Path) -> list[Path]:
    return [write_pdf(root / f"{i}.pdf", [PAGE_1, PAGE_2]) for i in range(1, 6)]


def test_document_is_stored_with_meeting_people_topics_and_chunks(
    conn: psycopg.Connection, root: Path, minutes: Path
) -> None:
    result = index_file(conn, minutes, root, FakeAnalyzer(), MODEL)

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
    index_file(conn, minutes, root, FakeAnalyzer(), MODEL)
    rows = conn.execute(
        "SELECT title FROM topics WHERE search_tsv @@ websearch_to_tsquery('finnish', 'verkkosivujen')"
    ).fetchall()
    assert rows == [("Seuran verkkosivut",)]


def test_each_document_is_committed_on_its_own(
    conn: psycopg.Connection, database_url: str, root: Path, minutes: Path
) -> None:
    index_file(conn, minutes, root, FakeAnalyzer(), MODEL)
    with psycopg.connect(database_url) as other:  # a separate session sees it immediately
        assert other.execute("SELECT count(*) FROM meetings").fetchone() == (1,)


def test_unchanged_document_is_skipped(conn: psycopg.Connection, root: Path, minutes: Path) -> None:
    extractor = FakeAnalyzer()
    index_file(conn, minutes, root, extractor, MODEL)

    assert index_file(conn, minutes, root, extractor, MODEL).status == "skipped"
    assert extractor.calls == 1


@pytest.mark.parametrize("change", ["file", "model", "force"])
def test_document_is_reindexed_when_something_changed(
    conn: psycopg.Connection, root: Path, minutes: Path, change: str
) -> None:
    extractor = FakeAnalyzer()
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
    analyzer = FakeAnalyzer()

    assert index_file(conn, scan, root, analyzer, MODEL).status == "no_text"
    assert analyzer.calls == 0
    assert [(p.rel_path, p.status) for p in db.problem_documents(conn)] == [("scan.pdf", "no_text")]
    assert index_file(conn, scan, root, analyzer, MODEL).status == "skipped"


def test_failure_is_recorded_and_keeps_the_earlier_extraction(
    conn: psycopg.Connection, root: Path, minutes: Path
) -> None:
    index_file(conn, minutes, root, FakeAnalyzer(), MODEL)

    failing = FakeAnalyzer(error=ConnectionError("ollama down"))
    result = index_file(conn, minutes, root, failing, MODEL, force=True)

    assert result.status == "failed"
    [problem] = db.problem_documents(conn)
    assert (problem.status, problem.error, problem.attempts) == ("failed", "ConnectionError: ollama down", 2)
    assert problem.duration_seconds is not None
    assert db.load_meeting(conn, "2019/hallitus-3-2019.pdf") is not None


def test_failed_document_is_not_retried_until_asked_or_changed(
    conn: psycopg.Connection, root: Path, minutes: Path
) -> None:
    index_file(conn, minutes, root, FakeAnalyzer(error=RuntimeError("broken")), MODEL)
    analyzer = FakeAnalyzer()

    assert index_file(conn, minutes, root, analyzer, MODEL).status == "skipped"
    assert index_file(conn, minutes, root, analyzer, MODEL, retry_failed=True).status == "indexed"
    assert analyzer.calls == 1

    index_file(conn, minutes, root, FakeAnalyzer(error=RuntimeError("broken")), MODEL, force=True)
    write_pdf(minutes, [PAGE_1, PAGE_2 + "\nKorjattu."])
    assert index_file(conn, minutes, root, analyzer, MODEL).status == "indexed"


def test_hanging_document_is_stopped_at_the_time_limit_and_recorded(
    conn: psycopg.Connection, root: Path, minutes: Path
) -> None:
    started = time.monotonic()

    result = index_file(conn, minutes, root, hanging_analyzer, MODEL)

    assert time.monotonic() - started < 10
    assert result.status == "timed_out"
    assert "time limit" in (result.error or "")
    assert status_of(conn, "2019/hallitus-3-2019.pdf") == ("timed_out", 1, True)
    assert index_file(conn, minutes, root, FakeAnalyzer(), MODEL).status == "skipped"


def test_unreadable_file_is_recorded_as_failed(conn: psycopg.Connection, root: Path) -> None:
    broken = root / "broken.pdf"
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"not a pdf")

    result = index_file(conn, broken, root, FakeAnalyzer(), MODEL)

    assert result.status == "failed"
    assert status_of(conn, "broken.pdf") == ("failed", 1, True)


def test_one_failing_document_does_not_stop_the_run(
    conn: psycopg.Connection, root: Path, many: list[Path]
) -> None:
    results = index_paths(conn, many, root, FailingFor("2.pdf"), MODEL)

    assert [r.status for r in results] == ["indexed", "failed", "indexed", "indexed", "indexed"]


def test_run_stops_after_consecutive_failures_and_leaves_the_rest_pending(
    conn: psycopg.Connection, root: Path, many: list[Path]
) -> None:
    with pytest.raises(RunStopped, match="3 documents in a row failed") as stopped:
        index_paths(
            conn, many, root, FailingFor("2.pdf", "3.pdf", "4.pdf"), MODEL, max_consecutive_failures=3
        )

    assert [r.status for r in stopped.value.results] == ["indexed", "failed", "failed", "failed"]
    assert status_of(conn, "5.pdf") == ("pending", 0, False)


def test_successes_reset_the_consecutive_failure_count(
    conn: psycopg.Connection, root: Path, many: list[Path]
) -> None:
    results = index_paths(
        conn, many, root, FailingFor("1.pdf", "3.pdf", "5.pdf"), MODEL, max_consecutive_failures=2
    )

    assert len(results) == 5


def test_run_stops_at_once_when_the_llm_service_is_down(
    conn: psycopg.Connection, root: Path, many: list[Path]
) -> None:
    with pytest.raises(RunStopped, match="not responding") as stopped:
        index_paths(conn, many, root, FailingFor("1.pdf"), MODEL, service_available=lambda: False)

    assert len(stopped.value.results) == 1
    pending = [p.rel_path for p in db.problem_documents(conn) if p.status == "pending"]
    assert pending == ["2.pdf", "3.pdf", "4.pdf", "5.pdf"]


def test_every_file_is_registered_before_processing_starts(
    conn: psycopg.Connection, root: Path, many: list[Path]
) -> None:
    seen: list[set[str]] = []

    def on_result(i: int, total: int, result) -> None:
        seen.append({path for path, (_, status) in db.stored_hashes(conn).items() if status == "pending"})

    index_paths(conn, many, root, FakeAnalyzer(), MODEL, on_result=on_result)

    assert seen[0] == {"2.pdf", "3.pdf", "4.pdf", "5.pdf"}
    assert seen[-1] == set()


def test_invalid_model_date_falls_back_to_the_text_with_a_warning(
    conn: psycopg.Connection, root: Path, minutes: Path
) -> None:
    result = index_file(conn, minutes, root, FakeAnalyzer(fake_meeting(date="2.4.2019")), MODEL)

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
    index_file(conn, first, root, FakeAnalyzer(fake_meeting(present=known, absent=[])), MODEL)
    variants = [Person(name="Jane Virtanen"), Person(name="Mika Virtanen"), Person(name="maija meikäläinen")]

    index_file(conn, second, root, FakeAnalyzer(fake_meeting(present=variants, absent=[])), MODEL)

    people = conn.execute("SELECT canonical_name FROM people ORDER BY canonical_name").fetchall()
    # "Jane" is a close misspelling of "Janne" (similarity 0.87) and case differences are ignored,
    # but "Mika" and "Mikko" (0.71) are different people and must stay apart.
    assert people == [("Janne Virtanen",), ("Maija Meikäläinen",), ("Mika Virtanen",), ("Mikko Virtanen",)]
    aliases = {row[0] for row in conn.execute("SELECT alias FROM person_aliases")}
    assert "Jane Virtanen" in aliases


def test_person_listed_twice_gives_a_warning(conn: psycopg.Connection, root: Path, minutes: Path) -> None:
    twice = fake_meeting(present=[Person(name="Teppo Testaaja")], absent=[Person(name="Teppo Testaaja")])

    result = index_file(conn, minutes, root, FakeAnalyzer(twice), MODEL)

    assert result.status == "indexed"
    assert any("more than once" in w for w in result.warnings)


def test_file_name_with_decomposed_umlaut_is_found_by_the_typed_name(
    conn: psycopg.Connection, root: Path
) -> None:
    # macOS can keep "ö" in a file name as "o" + combining diaeresis (NFD); users type the single "ö".
    decomposed = unicodedata.normalize("NFD", "pöytäkirja 2_2026.pdf")
    path = write_pdf(root / decomposed, [PAGE_1, PAGE_2])

    index_file(conn, path, root, FakeAnalyzer(), MODEL)

    assert db.load_meeting(conn, "pöytäkirja 2_2026.pdf") is not None
    assert db.get_document(conn, decomposed) is None  # stored in one form only
