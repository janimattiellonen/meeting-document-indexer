from meeting_indexer.search.highlight import MARK_END, MARK_START, Query, fragments, mark_all


def shown(text: str) -> str:
    return text.replace(MARK_START, "[").replace(MARK_END, "]")


def test_inflected_forms_of_a_query_word_are_marked() -> None:
    query = Query.of(["hallitus"])
    assert shown(mark_all("Paikalla oli hallituksesta 4/5.", query)) == "Paikalla oli [hallituksesta] 4/5."


def test_compound_parts_are_marked() -> None:
    assert shown(mark_all("Seuramestaruuskisoille haetaan päivää.", Query.of(["kisa"]))) == (
        "[Seuramestaruuskisoille] haetaan päivää."
    )


def test_words_starting_with_the_typed_query_are_marked() -> None:
    # "Kvarnbäckin": a name Voikko doesn't know, found as a prefix.
    assert shown(mark_all("Kvarnbäckin radalla", Query.of(["Kvarnbäck"]))) == "[Kvarnbäckin] radalla"


def test_other_words_and_punctuation_are_left_alone() -> None:
    assert mark_all("Ei osumia tässä, vain tekstiä.", Query.of(["kisa"])) == "Ei osumia tässä, vain tekstiä."


def test_short_text_is_returned_whole() -> None:
    assert shown(fragments("Kokous päätettiin klo 20.54.", Query.of(["kokous"]))) == (
        "[Kokous] päätettiin klo 20.54."
    )


def test_long_text_gives_excerpts_around_matches() -> None:
    filler = " ".join(f"sana{i}" for i in range(40))
    text = f"{filler} Hallitus kokoontui. {filler} Hallituksen jäsenet. {filler}"

    result = shown(fragments(text, Query.of(["hallitus"]), max_words=10))

    assert result.startswith("… ") and result.endswith(" …")
    assert result.count(" … ") == 1  # two excerpts
    assert "[Hallitus] kokoontui" in result
    assert "[Hallituksen] jäsenet" in result
    assert len(result.split()) < 30


def test_long_text_without_a_match_gives_its_start() -> None:
    text = " ".join(f"sana{i}" for i in range(40))
    assert fragments(text, Query.of(["kisa"]), max_words=5) == "sana0 sana1 sana2 sana3 sana4 …"


def test_words_whose_base_form_or_compound_part_starts_with_the_typed_query_are_marked() -> None:
    # Search matches "kis" as a prefix of the base forms and compound parts in lemma_tsv ("kisa").
    assert shown(mark_all("seuramestaruuskisoille kotisivu-uudistus", Query.of(["uudis", "kis"]))) == (
        "[seuramestaruuskisoille] [kotisivu-uudistus]"
    )


def test_excerpts_close_to_each_other_do_not_repeat_words() -> None:
    words = [f"w{i}" for i in range(60)]
    words[5] = words[30] = "hallitus"

    result = shown(fragments(" ".join(words), Query.of(["hallitus"])))

    shown_words = [w for w in result.split() if w != "…"]
    assert len(shown_words) == len(set(shown_words) - {"[hallitus]"}) + 2
    assert result.count("[hallitus]") == 2
