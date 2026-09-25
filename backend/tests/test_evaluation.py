from datetime import date, time

from meeting_indexer.db import AttendeeView, MeetingView, TopicView
from meeting_indexer.evaluation import golden_draft, score


def attendee(name: str, status: str, role: str | None) -> AttendeeView:
    return AttendeeView(name, status, role, person_id=1, person_name=name)


def meeting(**overrides) -> MeetingView:
    fields = {
        "title": "Hallituksen kokous 3/2019",
        "meeting_type": "board",
        "meeting_number": "3/2019",
        "term_year": 2019,
        "meeting_date": date(2019, 4, 2),
        "start_time": time(18, 0),
        "end_time": None,
        "location": "Seuratalo",
        "summary": "…",
        "warnings": [],
        "attendees": [
            attendee("Maija Meikäläinen", "present", "puheenjohtaja"),
            attendee("Teppo Testaaja", "present", None),
            attendee("Liisa Laine", "absent", None),
        ],
        "topics": [
            TopicView("1", "Kokouksen avaus", None, None, 1, id=1),
            TopicView("2", "Talous", None, None, 1, id=2),
        ],
        "id": 1,
        "document_id": 1,
        "rel_path": "a.pdf",
        "file_type": "pdf",
        "page_count": 2,
    }
    return MeetingView(**(fields | overrides))


def test_perfect_extraction_scores_full_marks() -> None:
    result = score(golden_draft(meeting(), "a.pdf"), meeting())
    assert all(result.checks.values())
    assert result.present == result.absent == result.topics == (1.0, 1.0)


def test_mistakes_are_reported_per_field() -> None:
    golden = golden_draft(meeting(), "a.pdf")
    actual = meeting(
        meeting_date=date(2019, 4, 3),
        attendees=[
            attendee("Maija Meikäläinen", "present", None),
            attendee("Keksitty Henkilö", "present", None),
        ],
        topics=[TopicView("1", "Kokouksen avaus.", None, None, 1, id=1)],
    )

    result = score(golden, actual)

    assert result.checks["date"] is False
    assert result.checks["topic_count"] is False
    assert result.checks["location"] is True
    assert result.present == (0.5, 0.5)  # one invented person, one missed
    assert result.absent == (0.0, 0.0)
    assert result.topics == (1.0, 0.5)  # "Kokouksen avaus." is close enough to "Kokouksen avaus"


def test_missing_document_is_flagged() -> None:
    assert score({"rel_path": "a.pdf"}, None).missing
