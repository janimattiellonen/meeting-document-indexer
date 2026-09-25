"""Finnish base forms with Voikko (libvoikko, `brew install libvoikko`), for search.

Postgres' Snowball stemmer misses stem changes: "hallituksen" stems to hallituks, "hallitus" to hallitus,
so they never match. Voikko analyses each word into its base form (hallituksen -> hallitus,
kisoille -> kisa, paitoja -> paita) and the parts of compound words
(seuramestaruuskisoille -> seura, mestaruus, kisa).

- Documents are indexed with base forms *and* compound parts, so "kisa" finds "seuramestaruuskisa".
- Queries use base forms only: "kotisivut" should find "kotisivu", not every "koti".
Words Voikko doesn't know (names, "Qwertix") are kept as written, lowercased.
"""

import re
import threading
from functools import lru_cache

import libvoikko

# A word: letters and digits, with inner hyphens ("t-paita", "kotisivu-uudistus").
WORD = re.compile(r"\w+(?:-\w+)*")
# One morpheme of Voikko's WORDBASES: "+verkko(verkko)" -> surface "verkko", base "verkko".
# Derivational suffixes have a base starting with "+": "+us(+us)".
MORPHEME = re.compile(r"\+([^+(]*)\(([^)]*)\)")

_voikko: libvoikko.Voikko | None = None
# A Voikko instance isn't safe to share between threads, and the API serves requests from a thread pool.
_lock = threading.Lock()


class VoikkoUnavailable(RuntimeError):
    pass


def _analyze(word: str) -> list[dict[str, str]]:
    global _voikko
    with _lock:
        if _voikko is None:
            try:
                _voikko = libvoikko.Voikko("fi")
            except (OSError, libvoikko.VoikkoException) as e:
                raise VoikkoUnavailable(
                    f"Voikko is not available ({e}). Install it with `brew install libvoikko`."
                ) from e
        return _voikko.analyze(word)


def check_available() -> None:
    """Raises VoikkoUnavailable with installation instructions if Voikko can't be loaded."""
    _analyze("kokous")


def available() -> bool:
    """Whether Voikko can be loaded. Search uses this to fall back to prefix matching without it."""
    try:
        check_available()
    except VoikkoUnavailable:
        return False
    return True


@lru_cache(maxsize=200_000)
def analyses(word: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(base forms, compound parts) of one word, lowercased. Unknown words give ((word,), ())."""
    bases: list[str] = []
    parts: list[str] = []
    for analysis in _analyze(word):
        base = analysis.get("BASEFORM")
        if not base:
            continue
        bases.append(base.lower())
        # "kotisivu-uudistus": the hyphen joins two words, each worth finding on its own.
        parts.extend(p.lower() for p in base.split("-") if len(p) >= 2 and "-" in base)
        parts.extend(_compound_parts(analysis.get("WORDBASES", "")))
    if not bases:
        return (word.lower(),), ()
    return _unique(bases), tuple(p for p in _unique(parts) if p not in bases)


def _compound_parts(wordbases: str) -> list[str]:
    """The word-level parts of a compound: "+seura(seura)+mestaruus(mestaruus)+kisa(kisa)" -> all three.

    A part followed by a derivational suffix ("+uudist(uudistaa)+us(+us)") is the stem of a derived word,
    not a word of the compound, so it's left out; so is anything that isn't a compound at all.
    """
    morphemes = MORPHEME.findall(wordbases)
    words = [
        base.lower()
        for i, (_, base) in enumerate(morphemes)
        if not base.startswith("+")
        and len(base) >= 2
        and not (i + 1 < len(morphemes) and morphemes[i + 1][1].startswith("+"))
    ]
    return words if len(words) >= 2 else []


def _unique(items: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))


def words(text: str) -> list[str]:
    return WORD.findall(text)


def document_lemmas(text: str | None) -> str:
    """Base forms and compound parts of every word in text, space-separated, for to_tsvector('simple')."""
    if not text:
        return ""
    out: list[str] = []
    for word in words(text):
        bases, parts = analyses(word)
        out.extend(bases)
        out.extend(parts)
    return " ".join(out)


def query_forms(word: str) -> tuple[str, ...]:
    """The base forms a query word can match. Several when the word is ambiguous ("kuusi")."""
    return analyses(word)[0]


def matching_forms(word: str) -> set[str]:
    """Everything a document word can be matched by: its base forms and compound parts."""
    bases, parts = analyses(word)
    return {*bases, *parts}


@lru_cache(maxsize=10_000)
def is_first_name(word: str) -> bool:
    """Whether Voikko knows the word as a first name ("Antti"; "Kari" is also a surname and a noun)."""
    return any(a.get("CLASS") == "etunimi" for a in _analyze(word))
