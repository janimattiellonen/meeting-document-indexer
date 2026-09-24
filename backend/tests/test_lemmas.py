"""Finnish base forms with Voikko (needs `brew install libvoikko`)."""

import pytest

from meeting_indexer.search.lemmas import analyses, document_lemmas, matching_forms, query_forms


@pytest.mark.parametrize(
    ("word", "base"),
    [
        # Stem changes and consonant gradation, which Postgres' Snowball stemmer misses:
        ("hallituksen", "hallitus"),
        ("kokouksessa", "kokous"),
        ("kisoille", "kisa"),
        ("paitoja", "paita"),
        ("talkoisiin", "talkoo"),
        ("junioreiden", "juniori"),
        ("Hallitus", "hallitus"),
    ],
)
def test_inflected_words_get_their_base_form(word: str, base: str) -> None:
    assert base in analyses(word)[0]


def test_compound_words_also_give_their_parts() -> None:
    assert analyses("seuramestaruuskisoille") == (("seuramestaruuskisa",), ("seura", "mestaruus", "kisa"))
    assert matching_forms("verkkosivujen") == {"verkkosivu", "verkko", "sivu"}


def test_hyphenated_compounds_give_each_word() -> None:
    bases, parts = analyses("kotisivu-uudistus")
    assert bases == ("kotisivu-uudistus",)
    assert {"kotisivu", "uudistus", "koti", "sivu"} <= set(parts)
    # "uudistus" is derived from "uudistaa"; the verb stem isn't a word of the compound.
    assert "uudistaa" not in parts


def test_derived_words_are_not_split() -> None:
    assert analyses("luettavan") == (("luettava",), ())


def test_unknown_words_are_kept_as_written_in_lowercase() -> None:
    assert analyses("Qwertix") == (("qwertix",), ())


def test_ambiguous_words_give_every_reading() -> None:
    assert set(query_forms("kuusi")) == {"kuusi", "kuu"}


def test_queries_use_base_forms_only() -> None:
    # "kotisivut" should find "kotisivu", not every document that mentions "koti".
    assert query_forms("kotisivut") == ("kotisivu",)


def test_document_lemmas_cover_every_word() -> None:
    # "uusi" is also a form of the verb "uusia"; both readings are indexed.
    assert document_lemmas("Hankitaan seuralle uusi tietokone.") == (
        "hankkia seura uusi uusia tietokone tieto kone"
    )
    assert document_lemmas(None) == ""
    assert document_lemmas("") == ""
