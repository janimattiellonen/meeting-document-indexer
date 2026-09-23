"""Measuring extraction quality against hand-checked "golden" files.

A golden file (data/eval/*.json, never committed) describes what one document should produce:

    {"rel_path": "2019/hallitus-3-2019.pdf", "title": "Hallituksen kokous 3/2019",
     "meeting_type": "board", "date": "2019-04-02", "start_time": "18:00", "location": "Seuratalo",
     "present": ["Maija Meikäläinen", …], "absent": […], "topics": ["Kokouksen avaus", …]}

The indexed result is read from the database, so run `mi index` first.
"""

import json
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import psycopg

from meeting_indexer import db
from meeting_indexer.extract import normalize_path

TOPIC_MATCH_RATIO = 0.8


@dataclass
class DocumentScore:
    rel_path: str
    checks: dict[str, bool]  # single-value fields: correct or not
    present: tuple[float, float]  # precision, recall
    absent: tuple[float, float]
    topics: tuple[float, float]
    missing: bool = False


def _same(a: str | None, b: str | None) -> bool:
    return (a or "").strip().casefold() == (b or "").strip().casefold()


def _precision_recall(expected: list[str], actual: list[str], similar) -> tuple[float, float]:
    if not expected and not actual:
        return 1.0, 1.0
    matched_actual = sum(1 for a in actual if any(similar(a, e) for e in expected))
    matched_expected = sum(1 for e in expected if any(similar(a, e) for a in actual))
    precision = matched_actual / len(actual) if actual else 0.0
    recall = matched_expected / len(expected) if expected else 0.0
    return precision, recall


def _similar_title(a: str, b: str) -> bool:
    return SequenceMatcher(None, a.casefold(), b.casefold()).ratio() >= TOPIC_MATCH_RATIO


def score(golden: dict, actual: db.MeetingView | None) -> DocumentScore:
    if actual is None:
        return DocumentScore(golden["rel_path"], {}, (0, 0), (0, 0), (0, 0), missing=True)
    checks = {
        "title": _same(golden.get("title"), actual.title),
        "meeting_type": golden.get("meeting_type") == actual.meeting_type,
        "date": golden.get("date") == (actual.meeting_date.isoformat() if actual.meeting_date else None),
        "start_time": golden.get("start_time")
        == (actual.start_time.strftime("%H:%M") if actual.start_time else None),
        "location": _same(golden.get("location"), actual.location),
        "topic_count": len(golden.get("topics", [])) == len(actual.topics),
    }
    return DocumentScore(
        golden["rel_path"],
        checks,
        present=_precision_recall(golden.get("present", []), actual.names("present"), _same),
        absent=_precision_recall(golden.get("absent", []), actual.names("absent"), _same),
        topics=_precision_recall(golden.get("topics", []), [t.title for t in actual.topics], _similar_title),
    )


def load_golden(directory: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.json"))]


def evaluate(conn: psycopg.Connection, directory: Path) -> list[DocumentScore]:
    return [score(g, db.load_meeting(conn, normalize_path(g["rel_path"]))) for g in load_golden(directory)]


def golden_draft(meeting: db.MeetingView, rel_path: str) -> dict:
    """A golden file pre-filled from the current extraction, to be checked and corrected by hand."""
    return {
        "rel_path": rel_path,
        "title": meeting.title,
        "meeting_type": meeting.meeting_type,
        "date": meeting.meeting_date.isoformat() if meeting.meeting_date else None,
        "start_time": meeting.start_time.strftime("%H:%M") if meeting.start_time else None,
        "location": meeting.location,
        "present": meeting.names("present"),
        "absent": meeting.names("absent"),
        "topics": [t.title for t in meeting.topics],
    }
