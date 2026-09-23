"""Full-text search over agenda items and document text, grouped by meeting.

Each query word matches in any of three ways, OR'ed together:
- by its Voikko base form, against the base forms and compound parts indexed in `lemma_tsv`
  ("hallitus" finds "hallituksen", "kisa" finds "seuramestaruuskisoille"; see lemmas.py);
- as a prefix stemmed by Postgres' `finnish` configuration ("junioreiden" -> junior:*);
- as a prefix as typed ("verkko" -> verkko:*, "Kvarnbäck" -> Kvarnbäckin). This also covers words Voikko
  doesn't know, such as names.
All words must match, except Finnish stopwords ("ja", "on"), which the index leaves out and the query
drops. Matches are marked by highlight.py, which follows the same rules.
If Voikko can't be loaded, the base-form alternative is left out and the two prefix ways still match.
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

import psycopg
from psycopg import sql

from meeting_indexer.llm import MeetingType
from meeting_indexer.search import highlight, lemmas

# Highlight markers in snippets. The frontend splits on them and renders plain text, so nothing from a
# document is ever rendered as HTML. Extraction doesn't strip these control characters, but text rarely
# contains them; if a document did, the worst case is a wrongly highlighted span.
MARK_START, MARK_END = highlight.MARK_START, highlight.MARK_END

log = logging.getLogger(__name__)

MAX_MEETINGS = 50
# A match in the raw text counts less than one in an agenda item's title, decision or description.
CHUNK_WEIGHT = 0.5

Sort = Literal["relevance", "newest", "oldest"]


def search_words(query: str) -> list[str]:
    """The searchable words of a query: letters, digits and inner hyphens, at least two characters.

    Split the way highlight.py splits the text it marks (lemmas.WORD).
    """
    words = [w.strip("_") for w in lemmas.words(query)]
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


def tsquery(words: list[str], *, base_forms: bool = True) -> sql.Composable:
    """All words must match; each one by a base form, or as a stemmed or an unstemmed prefix.

    Searched against `search_tsv || lemma_tsv`: prefixes match the stemmed lexemes of search_tsv and the
    base forms of lemma_tsv alike. base_forms=False leaves out the Voikko alternative.
    """
    return sql.SQL(" && ").join(
        sql.SQL("({alternatives})").format(
            alternatives=sql.SQL(" || ").join(
                [
                    sql.SQL("to_tsquery('finnish', {p})").format(p=sql.Literal(f"{word}:*")),
                    sql.SQL("to_tsquery('simple', {p})").format(p=sql.Literal(f"{word}:*")),
                    *(
                        sql.SQL("plainto_tsquery('simple', {f})").format(f=sql.Literal(form))
                        for form in (lemmas.query_forms(word) if base_forms else ())
                    ),
                ]
            )
        )
        for word in words
    )


def topic_snippet(decisions: str | None, summary: str | None, query: highlight.Query) -> str:
    # The decision is often repeated word for word in the description.
    parts = [decisions if decisions and decisions not in (summary or "") else None, summary]
    return highlight.fragments(" ".join(p for p in parts if p), query)


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
    meeting_type: MeetingType
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
    meeting_type: MeetingType | None = None,
    sort: Sort = "relevance",
) -> list[MeetingHit]:
    words = drop_stopwords(conn, search_words(query))
    if not words:
        return []
    params = {
        "year_from": year_from,
        "year_to": year_to,
        "meeting_type": meeting_type,
    }
    base_forms = lemmas.available()
    if not base_forms:
        log.warning(
            "Voikko is not available; searching by prefix only. Install it with `brew install libvoikko`."
        )
    matches = tsquery(words, base_forms=base_forms)

    topic_rows = conn.execute(
        sql.SQL(f"""
        SELECT t.meeting_id, t.id, t.item_number, t.title, t.decisions, t.summary,
               t.page_no, ts_rank_cd(t.search_tsv || t.lemma_tsv, q)
        FROM topics t
        JOIN meetings m ON m.id = t.meeting_id,
             (SELECT {{query}} AS q) AS search_query
        WHERE (t.search_tsv || t.lemma_tsv) @@ q {FILTERS}
        ORDER BY t.meeting_id, t.ordinal
        """).format(query=matches),
        params,
    ).fetchall()

    chunk_rows = conn.execute(
        sql.SQL(f"""
        SELECT m.id, c.page_no, c.text, ts_rank_cd(c.search_tsv || c.lemma_tsv, q)
        FROM chunks c
        JOIN meetings m ON m.document_id = c.document_id,
             (SELECT {{query}} AS q) AS search_query
        WHERE (c.search_tsv || c.lemma_tsv) @@ q {FILTERS}
        ORDER BY m.id, c.ordinal
        """).format(query=matches),
        params,
    ).fetchall()

    marks = highlight.Query.of(words, base_forms=base_forms)
    scores: dict[int, float] = {}
    topics: dict[int, list[TopicHit]] = {}
    for meeting_id, topic_id, number, title, decisions, summary, page_no, rank in topic_rows:
        topics.setdefault(meeting_id, []).append(
            TopicHit(
                topic_id,
                number,
                highlight.mark_all(title, marks),
                topic_snippet(decisions, summary, marks),
                page_no,
                decisions is not None,
            )
        )
        scores[meeting_id] = max(scores.get(meeting_id, 0.0), rank)
    text: dict[int, list[TextHit]] = {}
    for meeting_id, page_no, chunk_text, rank in chunk_rows:
        if len(text.get(meeting_id, [])) < 2:  # only the first two are shown
            text.setdefault(meeting_id, []).append(TextHit(page_no, highlight.fragments(chunk_text, marks)))
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
