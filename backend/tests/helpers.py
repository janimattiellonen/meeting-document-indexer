"""Test helpers shared by the pipeline and API tests. All names and content are invented."""

from pathlib import Path

import pymupdf

from meeting_indexer.extract import extract_pages, has_text
from meeting_indexer.indexer import Analysis
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


class FakeAnalyzer:
    """Reads the real text, but returns a canned LLM answer (or raises) instead of calling the model."""

    def __init__(self, meeting: Meeting | None = None, error: Exception | None = None) -> None:
        self.meeting = meeting or fake_meeting()
        self.error = error
        self.calls = 0

    def __call__(self, path: Path) -> Analysis:
        pages = extract_pages(path)
        if not has_text(pages):
            return Analysis(pages, None)
        self.calls += 1
        if self.error:
            raise self.error
        return Analysis(pages, self.meeting)


def write_pdf(path: Path, pages: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = pymupdf.open()
    for text in pages:
        page = pdf.new_page()
        if text:
            page.insert_text((72, 72), text, fontname="helv")
    pdf.save(path)
    return path
