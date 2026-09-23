"""Command-line helpers that don't need the database or the LLM."""

from pathlib import Path

import pytest
import typer

from meeting_indexer import db
from meeting_indexer.cli import changed_files, resolve_targets
from meeting_indexer.extract import sha256


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


@pytest.mark.parametrize("state", ["indexed", "no_text", "failed", "timed_out"])
def test_a_changed_file_is_listed_whatever_its_last_outcome(root: Path, state: db.DocumentStatus) -> None:
    path = root / "2019" / "hallitus-3-2019.pdf"
    stored: dict[str, tuple[str | None, db.DocumentStatus]] = {
        "2019/hallitus-3-2019.pdf": (sha256(path), state)
    }
    assert changed_files(stored, {"2019/hallitus-3-2019.pdf": path}) == ([], [])

    path.write_bytes(b"%PDF korjattu")
    assert changed_files(stored, {"2019/hallitus-3-2019.pdf": path}) == (["2019/hallitus-3-2019.pdf"], [])


def test_pending_and_missing_files_are_not_listed_as_changed(root: Path) -> None:
    path = root / "2019" / "hallitus-3-2019.pdf"
    stored = {"2019/hallitus-3-2019.pdf": (None, "pending"), "2019/poistettu.pdf": ("x", "indexed")}
    assert changed_files(stored, {"2019/hallitus-3-2019.pdf": path}) == ([], [])


def test_an_unreadable_file_is_listed_instead_of_stopping_the_comparison(root: Path) -> None:
    locked, readable = root / "2019" / "hallitus-3-2019.pdf", root / "2019" / "hallitus-4-2019.pdf"
    readable.write_bytes(b"%PDF")
    stored: dict[str, tuple[str | None, db.DocumentStatus]] = {
        "2019/hallitus-3-2019.pdf": (sha256(locked), "failed"),
        "2019/hallitus-4-2019.pdf": ("vanha", "indexed"),
    }
    locked.chmod(0)
    try:
        changed, unreadable = changed_files(
            stored, {"2019/hallitus-3-2019.pdf": locked, "2019/hallitus-4-2019.pdf": readable}
        )
    finally:
        locked.chmod(0o644)

    assert changed == ["2019/hallitus-4-2019.pdf"]
    [line] = unreadable
    assert line.startswith("2019/hallitus-3-2019.pdf: PermissionError")
