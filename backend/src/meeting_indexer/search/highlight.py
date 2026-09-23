"""Marking query matches in text, the way the search matched them.

Postgres' ts_headline only knows its own stemming, so it wouldn't mark "hallituksen" for a query that
matched it through its base form "hallitus". This marks a word when one of its base forms or compound
parts is a base form of a query word, or when it starts with a query word as typed.
Without base forms (Voikko not available), only the prefix as typed is marked, as search matches then.
"""

from dataclasses import dataclass

from meeting_indexer.search import lemmas

MARK_START, MARK_END = "\x02", "\x03"
FRAGMENT_DELIMITER = " … "


@dataclass
class Query:
    words: list[str]  # as typed
    forms: set[str]  # base forms of every query word
    base_forms: bool = True  # False: match prefixes only, without Voikko

    @classmethod
    def of(cls, words: list[str], *, base_forms: bool = True) -> "Query":
        forms = {form for word in words for form in lemmas.query_forms(word)} if base_forms else set()
        return cls(words, forms, base_forms)

    def matches(self, word: str) -> bool:
        lowered = word.lower()
        if any(lowered.startswith(w.lower()) for w in self.words):
            return True
        return self.base_forms and bool(lemmas.matching_forms(word) & self.forms)


def mark_all(text: str, query: Query) -> str:
    """The whole text, with every matching word marked."""
    out: list[str] = []
    position = 0
    for match in lemmas.WORD.finditer(text):
        out.append(text[position : match.start()])
        word = match.group()
        out.append(f"{MARK_START}{word}{MARK_END}" if query.matches(word) else word)
        position = match.end()
    out.append(text[position:])
    return "".join(out)


def fragments(text: str, query: Query, *, max_words: int = 24, max_fragments: int = 2) -> str:
    """Up to max_fragments excerpts of about max_words words around matches, joined with " … ".

    Short texts are returned whole. With no match (it can happen when only the stemmed form matched),
    the start of the text is returned.
    """
    spans = [(m.start(), m.end(), m.group()) for m in lemmas.WORD.finditer(text)]
    if len(spans) <= max_words:
        return mark_all(text, query).strip()
    hits = [i for i, (_, _, word) in enumerate(spans) if query.matches(word)]
    if not hits:
        return _excerpt(text, spans, 0, max_words, query) + FRAGMENT_DELIMITER.rstrip()

    windows: list[tuple[int, int]] = []
    for hit in hits:
        if windows and hit < windows[-1][1]:
            continue  # already inside the previous excerpt
        start = max(0, min(hit - max_words // 3, len(spans) - max_words))
        windows.append((start, start + max_words))
        if len(windows) == max_fragments:
            break
    excerpts = [_excerpt(text, spans, start, end - start, query) for start, end in windows]
    prefix = FRAGMENT_DELIMITER.lstrip() if windows[0][0] > 0 else ""
    suffix = FRAGMENT_DELIMITER.rstrip() if windows[-1][1] < len(spans) else ""
    return prefix + FRAGMENT_DELIMITER.join(excerpts) + suffix


def _excerpt(text: str, spans: list[tuple[int, int, str]], start: int, count: int, query: Query) -> str:
    first, last = spans[start], spans[min(start + count, len(spans)) - 1]
    return mark_all(text[first[0] : last[1]], query)
