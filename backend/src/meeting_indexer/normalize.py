"""Validating and repairing LLM output against the document text."""

import re
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from difflib import SequenceMatcher

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
    warnings.add(f"date {llm_value!r} from the model is missing or invalid; using {fallback} from the text")
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
    return re.sub(r"\s+", " ", name).strip()


def clean_role(role: str | None) -> str | None:
    cleaned = clean_name(role or "").casefold()
    return cleaned or None
