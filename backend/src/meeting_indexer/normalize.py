"""Validating and repairing LLM output against the document text."""

import re
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from difflib import SequenceMatcher

from meeting_indexer.llm import MeetingType

EARLIEST_DATE = date(2000, 1, 1)
FINNISH_DATE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d)")
TIME = re.compile(r"^\s*(?:klo\s*)?(\d{1,2})(?:[.:](\d{2}))?\s*$", re.IGNORECASE)
TITLE_MATCH_RATIO = 0.8
# "ESIMERKKISEURA RY Hallituksen kokous 4/2026": the association's letterhead before the title.
LETTERHEAD = re.compile(r"^\s*\S.*?\s+ry\s+(?=\S)", re.IGNORECASE)
LEADING_ITEM_NUMBER = re.compile(r"^\s*(\d+[a-z]?)[.)]\s+(.+)$", re.IGNORECASE | re.DOTALL)


@dataclass
class Warnings:
    items: list[str] = field(default_factory=list)

    def add(self, message: str) -> None:
        self.items.append(message)


def plausible(value: date, today: date) -> bool:
    return EARLIEST_DATE <= value <= today + timedelta(days=30)


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def first_date_in_text(text: str, today: date) -> date | None:
    for day, month, year in FINNISH_DATE.findall(text):
        try:
            found = date(int(year), int(month), int(day))
        except ValueError:
            continue
        if plausible(found, today):
            return found
    return None


def meeting_date(llm_value: str | None, first_page: str, warnings: Warnings, today: date) -> date | None:
    """The model's date if it's valid and plausible, else the first date in the document's first page."""
    parsed = parse_iso_date(llm_value)
    if parsed and plausible(parsed, today):
        return parsed
    fallback = first_date_in_text(first_page, today)
    found = f"using {fallback} from the text" if fallback else "no date found in the text"
    warnings.add(f"date {llm_value!r} from the model is missing or invalid; {found}")
    return fallback


def parse_time(value: str | None) -> time | None:
    if not value:
        return None
    match = TIME.match(value)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    return time(hour, minute) if hour < 24 and minute < 60 else None


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _best_line_ratio(title: str, page: str) -> float:
    lines = (_normalize(line) for line in page.splitlines())
    return max((SequenceMatcher(None, title, line).ratio() for line in lines), default=0)


def topic_pages(titles: list[str], pages: list[str], warnings: Warnings) -> list[int | None]:
    """Page number (1-based) for each topic title, found in the text rather than trusted from the model.

    Topics appear in document order, so the search for each one starts from the previous topic's page.
    """
    normalized_pages = [_normalize(p) for p in pages]
    result: list[int | None] = []
    start = 0
    for title in titles:
        wanted = _normalize(title)
        found = next((i for i in range(start, len(pages)) if wanted in normalized_pages[i]), None)
        if found is None:
            ratios = [(i, _best_line_ratio(wanted, pages[i])) for i in range(start, len(pages))]
            best = max(ratios, key=lambda r: r[1], default=(None, 0.0))
            if best[1] >= TITLE_MATCH_RATIO:
                found = best[0]
        if found is None:
            warnings.add(f"topic title not found in the text: {title!r}")
            result.append(None)
        else:
            result.append(found + 1)
            start = found
    return result


def chunk_pages(pages: list[str], max_chars: int = 3000) -> list[tuple[int, str]]:
    """Split page text into (page_no, text) chunks of whole lines, at most ~max_chars each.

    Chunks never span pages, so every chunk can link to its page.
    """
    chunks: list[tuple[int, str]] = []
    for page_no, page in enumerate(pages, 1):
        current: list[str] = []
        length = 0
        for line in page.splitlines():
            if current and length + len(line) > max_chars:
                chunks.append((page_no, "\n".join(current).strip()))
                current, length = [], 0
            current.append(line)
            length += len(line) + 1
        text = "\n".join(current).strip()
        if text:
            chunks.append((page_no, text))
    return [(page_no, text) for page_no, text in chunks if text]


def clean_title(title: str) -> str:
    return LETTERHEAD.sub("", title, count=1).strip()


def split_item_number(title: str) -> tuple[str | None, str]:
    """("5", "Sihteerin kone") from "5. Sihteerin kone"; (None, title) if there is no leading number."""
    match = LEADING_ITEM_NUMBER.match(title)
    if not match:
        return None, title.strip()
    return match.group(1), match.group(2).strip()


def clean_name(name: str) -> str:
    """Whitespace collapsed, and stray punctuation from list formatting dropped ("Antti Esimerkki,")."""
    return re.sub(r"\s+", " ", name).strip().strip(",;:").strip()


def clean_role(role: str | None) -> str | None:
    cleaned = clean_name(role or "").casefold()
    return cleaned or None


# The meeting type as the minutes state it in their first lines ("HALLITUKSEN KOKOUS", "Yhdistyksen
# syyskokous"). The model mixed these up for nearly half of the documents, labelling most board meetings
# "autumn_general". Patterns match the stem "kokou", so inflected forms ("Syyskokouksen pöytäkirja") match.
# When several match, the one written first wins: "Hallituksen ylimääräinen kokous" is a board meeting,
# "Ylimääräinen yhdistyksen kokous" is not.
MEETING_KINDS: list[tuple[MeetingType, re.Pattern[str]]] = [
    ("board", re.compile(r"hallituksen\s+(?:\w+\s+)?\w*kokou", re.IGNORECASE)),
    ("spring_general", re.compile(r"kevätkokou", re.IGNORECASE)),
    ("autumn_general", re.compile(r"syyskokou", re.IGNORECASE)),
    ("extraordinary", re.compile(r"ylimääräi\w*\s+(?:yhdistyksen\s+)?kokou", re.IGNORECASE)),
    ("other", re.compile(r"yhdistyksen\s+kokou|vuosikokou", re.IGNORECASE)),
]
# The letterhead, title, date and place: the type is stated here, before the agenda starts.
HEADER_CHARS = 400
# "3/2019", "11/2019", "3-2/2014"; not part of a date or a longer number.
MEETING_NUMBER = re.compile(r"(?<![\d./-])(\d{1,2}(?:-\d{1,2})?)/((?:19|20)\d\d)(?!\d)")
# The board's first meeting, where it assigns its roles. Held late in the year, it's the next year's board.
ORGANIZING_MEETING = re.compile(r"järjestäytymiskokou", re.IGNORECASE)


@dataclass
class MeetingKind:
    meeting_type: MeetingType
    number: str | None  # as written: "3/2019"
    term_year: int | None


def _header(first_page: str) -> str:
    return re.sub(r"\s+", " ", first_page[:HEADER_CHARS])


def meeting_type_in(text: str) -> MeetingType | None:
    hits: list[tuple[int, MeetingType]] = [
        (m.start(), kind) for kind, pattern in MEETING_KINDS if (m := pattern.search(text))
    ]
    return min(hits)[1] if hits else None


def classify_meeting(
    title: str, first_page: str, meeting_date: date | None, model_type: MeetingType
) -> MeetingKind:
    """Type, number and term year of a meeting, from its header; the title and the model's type are fallbacks.

    The term year is the year the meeting belongs to. For a board meeting it's the board's term, from the
    meeting number ("1/2026" can be held in December 2025), except that an organizing meeting held in the
    autumn is the next year's board. For other meetings, and without a number, it's the year of the date.
    """
    header = _header(first_page)
    meeting_type = meeting_type_in(header) or meeting_type_in(title) or model_type
    number = MEETING_NUMBER.search(header) or MEETING_NUMBER.search(title)
    number_year = int(number.group(2)) if number else None
    date_year = meeting_date.year if meeting_date else None

    term_year = date_year or number_year
    if meeting_type == "board":
        organizing = ORGANIZING_MEETING.search(header) or ORGANIZING_MEETING.search(title)
        if organizing and meeting_date and meeting_date.month >= 9:
            term_year = meeting_date.year + 1
        elif number_year:
            term_year = number_year
    return MeetingKind(meeting_type, f"{number.group(1)}/{number.group(2)}" if number else None, term_year)
