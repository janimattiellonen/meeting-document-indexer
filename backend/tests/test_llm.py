from types import SimpleNamespace

import pytest

from meeting_indexer.llm import (
    MAX_CONTEXT,
    MAX_OUTPUT_TOKENS,
    MIN_CONTEXT,
    DocumentTooLong,
    Extractor,
    Meeting,
    RunawayGeneration,
    context_size,
    prompt_text,
)


def test_prompt_marks_page_breaks() -> None:
    assert prompt_text(["eka\n", " toka"]) == "[sivu 1]\neka\n\n[sivu 2]\ntoka"


def test_context_grows_with_the_document_but_has_a_floor() -> None:
    assert context_size("lyhyt") == MIN_CONTEXT
    assert context_size("x" * 70_000) > MIN_CONTEXT


def test_too_long_document_is_rejected_instead_of_silently_truncated() -> None:
    with pytest.raises(DocumentTooLong):
        context_size("x" * int(MAX_CONTEXT * 3.5))


class FakeClient:
    def __init__(self, content: str, done_reason: str) -> None:
        self.response = SimpleNamespace(message=SimpleNamespace(content=content), done_reason=done_reason)
        self.options: dict = {}

    def chat(self, **kwargs):
        self.options = kwargs["options"]
        return self.response


def extractor_with(client: FakeClient) -> Extractor:
    extractor = Extractor("http://127.0.0.1:11434", "fake")
    extractor.client = client  # pyright: ignore[reportAttributeAccessIssue]
    return extractor


def test_output_length_is_capped_and_a_runaway_generation_is_an_error() -> None:
    client = FakeClient('{"title": "Kokous", "topics": [{"title":', done_reason="length")

    with pytest.raises(RunawayGeneration):
        extractor_with(client)(["teksti"])
    assert client.options["num_predict"] == MAX_OUTPUT_TOKENS


def test_complete_answer_is_parsed() -> None:
    answer = Meeting(
        title="Kokous", meeting_type="board", present=[], absent=[], topics=[], summary="Lyhyt."
    ).model_dump_json()

    assert extractor_with(FakeClient(answer, done_reason="stop"))(["teksti"]).title == "Kokous"
