"""Full-text search over agenda items and document text, grouped by meeting.

Each query word is matched as a prefix in two forms, OR'ed together:
- stemmed by Postgres' `finnish` configuration ("junioreiden" -> junior:*), and
- as typed ("verkko" -> verkko:*). The stemmer turns "verkko" into "verko", which is not a prefix of
  "verkkosivut", so the stemmed form alone would miss it.
All words must match, except Finnish stopwords ("ja", "on"), which the index leaves out and the query
drops.
The Snowball stemmer still misses stem changes such as hallitus/hallituksen; Voikko lemmatisation is
the planned fix (docs/PLAN.md §8).
"""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

import psycopg
from psycopg import sql

# Highlight markers in snippets. The frontend splits on them and renders plain text, so nothing from a
# document is ever rendered as HTML. Extraction doesn't strip these control characters, but text rarely
# contains them; if a document did, the worst case is a wrongly highlighted span.
MARK_START, MARK_END = "\x02", "\x03"
HEADLINE = (
    f'StartSel="{MARK_START}", StopSel="{MARK_END}", MaxFragments=2, MinWords=6, MaxWords=24, '
    'FragmentDelimiter=" … "'
)
TITLE_HEADLINE = f'StartSel="{MARK_START}", StopSel="{MARK_END}", HighlightAll=true'

WORD = re.compile(r"[\w-]+")
MAX_MEETINGS = 50
# A match in the raw text counts less than one in an agenda item's title, decision or description.
CHUNK_WEIGHT = 0.5

Sort = Literal["relevance", "newest", "oldest"]


def search_words(query: str) -> list[str]:
    """The searchable words of a query: letters, digits and inner hyphens, at least two characters."""
    words = [w.strip("-_") for w in WORD.findall(query)]
    return [w for w in words if len(w) >= 2]


def drop_stopwords(conn: psycopg.Connection, words: list[str]) -> list[str]:
    """Words the `finnish` configuration keeps. Stopwords such as "ja" are left out of the index, so
    requiring one would make the whole query match nothing."""
    rows = conn.execute(
        """
        SELECT w FROM unnest(%s::text[]) WITH ORDINALITY AS u(w, i)
        WHERE to_tsvector('finnish', w) <> ''::tsvector ORDER BY i
        """,
        (words,),
    ).fetchall()
    return [w for (w,) in rows]


def tsquery(words: list[str]) -> sql.Composable:
    """All words must match; each one as a stemmed or an unstemmed prefix."""
    return sql.SQL(" && ").join(
        sql.SQL("(to_tsquery('finnish', {prefix}) || to_tsquery('simple', {prefix}))").format(
            prefix=sql.Literal(f"{word}:*")
        )
        for word in words
    )


@dataclass
class TopicHit:
    id: int
    item_number: str | None
    title: str  # with highlight markers
    snippet: str  # decision and description, with highlight markers
    page_no: int | None
    has_decision: bool


@dataclass
class TextHit:
    page_no: int | None
    snippet: str


@dataclass
class MeetingHit:
    meeting_id: int
    title: str
    meeting_type: str
    meeting_date: date | None
    document_id: int
    file_type: str
    score: float
    topics: list[TopicHit] = field(default_factory=list)
    text: list[TextHit] = field(default_factory=list)


FILTERS = """
    AND (%(year_from)s::int IS NULL OR extract(year FROM m.meeting_date) >= %(year_from)s)
    AND (%(year_to)s::int IS NULL OR extract(year FROM m.meeting_date) <= %(year_to)s)
    AND (%(meeting_type)s::text IS NULL OR m.meeting_type = %(meeting_type)s)
"""


def search(
    conn: psycopg.Connection,
    query: str,
    *,
    year_from: int | None = None,
    year_to: int | None = None,
    meeting_type: str | None = None,
    sort: Sort = "relevance",
) -> list[MeetingHit]:
    words = drop_stopwords(conn, search_words(query))
    if not words:
        return []
    params = {
        "headline": HEADLINE,
        "title_headline": TITLE_HEADLINE,
        "year_from": year_from,
        "year_to": year_to,
        "meeting_type": meeting_type,
    }

    topic_rows = conn.execute(
        sql.SQL(f"""
        SELECT t.meeting_id, t.id, t.item_number,
               ts_headline('finnish', t.title, q, %(title_headline)s),
               ts_headline('finnish', concat_ws(' ',
                   -- the decision is often repeated word for word in the description
                   CASE WHEN strpos(coalesce(t.summary, ''), t.decisions) = 0 THEN t.decisions END,
                   t.summary), q, %(headline)s),
               t.page_no, t.decisions IS NOT NULL, ts_rank_cd(t.search_tsv, q)
        FROM topics t
        JOIN meetings m ON m.id = t.meeting_id,
             (SELECT {{query}} AS q) AS search_query
        WHERE t.search_tsv @@ q {FILTERS}
        ORDER BY t.meeting_id, t.ordinal
        """).format(query=tsquery(words)),
        params,
    ).fetchall()

    chunk_rows = conn.execute(
        sql.SQL(f"""
        SELECT m.id, c.page_no, ts_headline('finnish', c.text, q, %(headline)s),
               ts_rank_cd(c.search_tsv, q)
        FROM chunks c
        JOIN meetings m ON m.document_id = c.document_id,
             (SELECT {{query}} AS q) AS search_query
        WHERE c.search_tsv @@ q {FILTERS}
        ORDER BY m.id, c.ordinal
        """).format(query=tsquery(words)),
        params,
    ).fetchall()

    scores: dict[int, float] = {}
    topics: dict[int, list[TopicHit]] = {}
    for meeting_id, topic_id, number, title, snippet, page_no, has_decision, rank in topic_rows:
        topics.setdefault(meeting_id, []).append(
            TopicHit(topic_id, number, title, snippet, page_no, has_decision)
        )
        scores[meeting_id] = max(scores.get(meeting_id, 0.0), rank)
    text: dict[int, list[TextHit]] = {}
    for meeting_id, page_no, snippet, rank in chunk_rows:
        text.setdefault(meeting_id, []).append(TextHit(page_no, snippet))
        scores[meeting_id] = max(scores.get(meeting_id, 0.0), rank * CHUNK_WEIGHT)
    if not scores:
        return []

    meetings = conn.execute(
        """
        SELECT m.id, m.title, m.meeting_type, m.meeting_date, d.id, d.file_type
        FROM meetings m JOIN documents d ON d.id = m.document_id
        WHERE m.id = ANY(%s)
        """,
        (list(scores),),
    ).fetchall()
    hits = [
        MeetingHit(
            meeting_id=mid,
            title=title,
            meeting_type=mtype,
            meeting_date=mdate,
            document_id=doc_id,
            file_type=file_type,
            score=scores[mid],
            topics=topics.get(mid, []),
            # The raw text is shown only when no agenda item matched; otherwise it repeats them.
            text=[] if mid in topics else text.get(mid, [])[:2],
        )
        for mid, title, mtype, mdate, doc_id, file_type in meetings
    ]

    if sort == "relevance":
        hits.sort(key=lambda h: (-h.score, h.meeting_date or date.min))
    else:
        dated = sorted((h for h in hits if h.meeting_date), key=lambda h: h.meeting_date or date.min)
        # Meetings without a date come last in both orders: they can't answer "when did this come up".
        hits = (dated[::-1] if sort == "newest" else dated) + [h for h in hits if not h.meeting_date]
    return hits[:MAX_MEETINGS]
