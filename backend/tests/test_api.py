"""The HTTP API against the test database, with documents indexed through the real pipeline."""

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from helpers import MODEL, PAGE_1, PAGE_2, FakeAnalyzer, fake_meeting, write_pdf

from meeting_indexer.api import create_app
from meeting_indexer.config import Settings
from meeting_indexer.indexer import index_file
from meeting_indexer.llm import Topic
from meeting_indexer.search import MARK_END, MARK_START, lemmas


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "documents"


@pytest.fixture
def indexed(conn: psycopg.Connection, root: Path) -> None:
    write_pdf(root / "2019" / "hallitus-3-2019.pdf", [PAGE_1, PAGE_2])
    index_file(conn, root / "2019" / "hallitus-3-2019.pdf", root, FakeAnalyzer(), MODEL)
    later = fake_meeting(
        title="Hallituksen kokous 5/2021",
        date="2021-09-01",
        topics=[Topic(title="1. Seuran verkkosivut", summary="Verkkosivut julkaistiin.")],
    )
    write_pdf(root / "2021" / "hallitus-5-2021.pdf", [PAGE_1.replace("3/2019", "5/2021"), PAGE_2])
    index_file(conn, root / "2021" / "hallitus-5-2021.pdf", root, FakeAnalyzer(later), MODEL)


@pytest.fixture
def client(database_url: str, root: Path) -> Iterator[TestClient]:
    settings = Settings(_env_file=None, database_url=database_url, docs_root=root)  # pyright: ignore[reportCallIssue]
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_search_groups_matching_agenda_items_by_meeting(client: TestClient, indexed: None) -> None:
    response = client.get("/api/search", params={"q": "verkkosivujen"})

    assert response.status_code == 200
    results = response.json()["results"]
    assert {r["title"] for r in results} == {"Hallituksen kokous 3/2019", "Hallituksen kokous 5/2021"}
    topic = next(r for r in results if r["title"].endswith("3/2019"))["topics"][0]
    assert topic["title"] == f"Seuran {MARK_START}verkkosivut{MARK_END}"
    assert topic["item_number"] == "2"
    assert topic["page_no"] == 1
    assert topic["has_decision"] is True


def test_search_finds_words_by_prefix(client: TestClient, indexed: None) -> None:
    results = client.get("/api/search", params={"q": "verkko"}).json()["results"]
    assert len(results) == 2


def test_search_filters_by_year_and_sorts_by_date(client: TestClient, indexed: None) -> None:
    only_2021 = client.get("/api/search", params={"q": "verkkosivut", "year_from": 2020}).json()
    assert [r["title"] for r in only_2021["results"]] == ["Hallituksen kokous 5/2021"]

    oldest = client.get("/api/search", params={"q": "verkkosivut", "sort": "oldest"}).json()
    assert [r["meeting_date"] for r in oldest["results"]] == ["2019-04-02", "2021-09-01"]


@pytest.mark.parametrize(
    ("sort", "expected"),
    [("oldest", ["2019-04-02", "2021-09-01", None]), ("newest", ["2021-09-01", "2019-04-02", None])],
)
def test_meetings_without_a_date_come_last_in_date_order(
    client: TestClient, conn: psycopg.Connection, root: Path, indexed: None, sort: str, expected: list
) -> None:
    undated = fake_meeting(title="Hallituksen kokous", date=None)
    # No date anywhere in the text either, so the indexer can't fill one in.
    write_pdf(root / "undated.pdf", ["Hallituksen kokous\n2. Seuran verkkosivut", PAGE_2])
    index_file(conn, root / "undated.pdf", root, FakeAnalyzer(undated), MODEL)

    results = client.get("/api/search", params={"q": "verkkosivut", "sort": sort}).json()["results"]

    assert [r["meeting_date"] for r in results] == expected


def test_search_falls_back_to_the_document_text(client: TestClient, indexed: None) -> None:
    # "Seuratalo" is only in the raw text, not in any agenda item.
    [result] = client.get("/api/search", params={"q": "seuratalo", "year_to": 2019}).json()["results"]
    assert result["topics"] == []
    assert MARK_START in result["text"][0]["snippet"]
    assert result["text"][0]["page_no"] == 1


def test_stopwords_in_the_query_are_ignored(client: TestClient, indexed: None) -> None:
    # The Finnish index leaves out words such as "ja"; requiring them would match nothing.
    results = client.get("/api/search", params={"q": "verkkosivut ja"}).json()["results"]
    assert len(results) == 2


def test_a_query_of_only_stopwords_gives_no_results(client: TestClient, indexed: None) -> None:
    assert client.get("/api/search", params={"q": "ja on"}).json()["results"] == []


@pytest.mark.parametrize("query", ["!!", "a", "   "])
def test_nothing_searchable_gives_no_results(client: TestClient, indexed: None, query: str) -> None:
    assert client.get("/api/search", params={"q": query}).json()["results"] == []


def test_search_requires_a_query(client: TestClient) -> None:
    assert client.get("/api/search").status_code == 422


@pytest.mark.parametrize("param", ["year_from", "year_to"])
@pytest.mark.parametrize("year", [1000000000000, 1899, 3000])
def test_search_rejects_an_impossible_year(client: TestClient, param: str, year: int) -> None:
    assert client.get("/api/search", params={"q": "kokous", param: year}).status_code == 422


def test_meetings_are_listed_newest_first(client: TestClient, indexed: None) -> None:
    meetings = client.get("/api/meetings").json()
    assert [m["title"] for m in meetings] == ["Hallituksen kokous 5/2021", "Hallituksen kokous 3/2019"]
    assert meetings[1]["topic_count"] == 3
    assert meetings[1]["decision_count"] == 1


def test_meeting_details(client: TestClient, indexed: None) -> None:
    meeting_id = client.get("/api/meetings").json()[1]["id"]

    meeting = client.get(f"/api/meetings/{meeting_id}").json()

    assert meeting["title"] == "Hallituksen kokous 3/2019"
    assert meeting["rel_path"] == "2019/hallitus-3-2019.pdf"
    assert [a["name"] for a in meeting["attendees"] if a["status"] == "absent"] == ["Liisa Laine"]
    assert [t["page_no"] for t in meeting["topics"]] == [1, 1, 2]


def test_unknown_meeting_is_404(client: TestClient) -> None:
    assert client.get("/api/meetings/999999").status_code == 404


def test_document_is_served_inline(client: TestClient, indexed: None) -> None:
    document_id = client.get("/api/meetings").json()[1]["document_id"]

    response = client.get(f"/api/documents/{document_id}/file")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline")
    assert response.content.startswith(b"%PDF")


def test_file_outside_the_documents_folder_is_never_served(
    client: TestClient, conn: psycopg.Connection, root: Path, tmp_path: Path
) -> None:
    (tmp_path / "secret.txt").write_text("not a meeting document")
    row = conn.execute(
        "INSERT INTO documents (rel_path, file_type, status)"
        " VALUES ('../secret.txt', 'txt', 'indexed') RETURNING id"
    ).fetchone()
    assert row is not None
    document_id = row[0]

    assert client.get(f"/api/documents/{document_id}/file").status_code == 404


def test_api_docs_pages_are_disabled(client: TestClient) -> None:
    # Their HTML loads scripts from a CDN; the schema itself stays available for type generation.
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 200


def test_search_finds_inflected_forms_through_their_base_form(client: TestClient, indexed: None) -> None:
    # "Hallituksen" stems to hallituks and "hallitus" to hallitus: only the base form connects them.
    [result] = client.get("/api/search", params={"q": "hallitus", "year_to": 2019}).json()["results"]
    assert f"{MARK_START}Hallituksen{MARK_END}" in result["text"][0]["snippet"]


def test_search_finds_compound_words_by_their_parts(client: TestClient, indexed: None) -> None:
    results = client.get("/api/search", params={"q": "sivu"}).json()["results"]
    titles = [t["title"] for r in results for t in r["topics"]]
    assert f"Seuran {MARK_START}verkkosivut{MARK_END}" in titles


def test_search_matches_agenda_item_titles_in_any_form(client: TestClient, indexed: None) -> None:
    results = client.get("/api/search", params={"q": "kokous", "year_to": 2019}).json()["results"]
    titles = [t["title"] for t in results[0]["topics"]]
    assert f"{MARK_START}Kokouksen{MARK_END} avaus" in titles


def test_search_without_voikko_still_matches_by_prefix(
    client: TestClient, indexed: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(word: str) -> list[dict[str, str]]:
        raise lemmas.VoikkoUnavailable("Voikko is not available (test).")

    monkeypatch.setattr(lemmas, "_analyze", unavailable)
    lemmas.analyses.cache_clear()

    response = client.get("/api/search", params={"q": "verkko"})

    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 2
    assert f"{MARK_START}verkkosivut{MARK_END}" in results[0]["topics"][0]["title"]


def test_people_are_listed_with_their_board_years(client: TestClient, indexed: None) -> None:
    listed = {p["name"]: p for p in client.get("/api/people").json()}

    maija = listed["Maija Meikäläinen"]
    assert (maija["meetings"], maija["first_year"], maija["last_year"]) == (2, 2019, 2021)
    assert maija["board_years"] == [2019, 2021]
    assert [p["name"] for p in client.get("/api/people", params={"q": "teppo"}).json()] == ["Teppo Testaaja"]


def test_person_details(client: TestClient, indexed: None) -> None:
    person_id = next(p["id"] for p in client.get("/api/people").json() if p["name"] == "Liisa Laine")

    person = client.get(f"/api/people/{person_id}").json()

    assert person["spellings"] == ["Liisa Laine"]
    assert [(m["meeting_date"], m["status"]) for m in person["meetings"]] == [
        ("2021-09-01", "absent"),
        ("2019-04-02", "absent"),
    ]
    assert [(t["year"], t["present"], t["absent"], t["meetings"]) for t in person["board_terms"]] == [
        (2019, 0, 1, 1),
        (2021, 0, 1, 1),
    ]
    assert client.get("/api/people/999999").status_code == 404


def test_board_of_a_year(client: TestClient, indexed: None) -> None:
    assert client.get("/api/boards").json() == [{"year": 2019, "meetings": 1}, {"year": 2021, "meetings": 1}]

    board = client.get("/api/boards/2019").json()

    assert [m["meeting_number"] for m in board["meetings"]] == ["3/2019"]
    assert [(m["name"], [r["name"] for r in m["roles"]]) for m in board["members"]] == [
        ("Maija Meikäläinen", ["puheenjohtaja"]),
        ("Teppo Testaaja", []),
        ("Liisa Laine", []),
    ]
    assert client.get("/api/boards/2020").status_code == 404
    assert client.get("/api/boards/20190").status_code == 422


def test_meeting_attendees_link_to_people(client: TestClient, indexed: None) -> None:
    meeting_id = client.get("/api/meetings").json()[1]["id"]
    attendee = client.get(f"/api/meetings/{meeting_id}").json()["attendees"][0]
    person = client.get(f"/api/people/{attendee['person_id']}").json()
    assert person["name"] == attendee["person_name"]
