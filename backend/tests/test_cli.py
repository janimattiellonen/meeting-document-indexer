"""Command-line helpers that don't need the database or the LLM."""

from pathlib import Path

import pytest
import typer

from meeting_indexer.cli import resolve_targets


@pytest.fixture
def root(tmp_path: Path) -> Path:
    docs = tmp_path / "documents"
    (docs / "2019").mkdir(parents=True)
    (docs / "2019" / "hallitus-3-2019.pdf").write_bytes(b"%PDF")
    (docs / "2019" / "muistiinpanot.txt").write_text("ei pöytäkirja", encoding="utf-8")
    return docs.resolve()


def test_no_paths_means_every_document_under_the_root(root: Path) -> None:
    assert resolve_targets(None, root) == [root / "2019" / "hallitus-3-2019.pdf"]


def test_a_directory_expands_to_its_documents(root: Path) -> None:
    assert resolve_targets([root / "2019"], root) == [root / "2019" / "hallitus-3-2019.pdf"]


def test_a_path_relative_to_the_root_is_accepted(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # not the root, so the path only makes sense relative to DOCS_ROOT
    assert resolve_targets([Path("2019/hallitus-3-2019.pdf")], root) == [
        root / "2019" / "hallitus-3-2019.pdf"
    ]


def test_a_missing_file_is_rejected(root: Path) -> None:
    with pytest.raises(typer.BadParameter, match="does not exist"):
        resolve_targets([root / "2019" / "hallitus-4-2019.pdf"], root)


def test_an_unsupported_file_is_rejected(root: Path) -> None:
    with pytest.raises(typer.BadParameter, match="not a supported document"):
        resolve_targets([root / "2019" / "muistiinpanot.txt"], root)


def test_a_path_outside_the_root_is_rejected(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "muu.pdf"
    outside.write_bytes(b"%PDF")
    with pytest.raises(typer.BadParameter, match="not under DOCS_ROOT"):
        resolve_targets([outside], root)
