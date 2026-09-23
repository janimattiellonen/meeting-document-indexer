"""Tests for scripts/guard_commit.py, the pre-commit hook that keeps real content out of git."""

import importlib.util
from pathlib import Path

import pytest

from meeting_indexer.config import REPO_ROOT

spec = importlib.util.spec_from_file_location("guard_commit", REPO_ROOT / "scripts" / "guard_commit.py")
assert spec and spec.loader
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

PATTERN = guard.names_pattern(["Maija Meikäläinen", "Teppo Testaaja"])


@pytest.mark.parametrize(
    "path",
    ["data/documents/x.pdf", "data/eval/golden.json", "minutes.pdf", "backend/old.DOC", ".env", ".env.local"],
)
def test_blocks_paths(path: str) -> None:
    assert guard.check(Path(path), None) is not None


@pytest.mark.parametrize("path", ["backend/tests/fixtures/synthetic.pdf", ".env.example", "docs/PLAN.md"])
def test_allows_paths(path: str) -> None:
    assert guard.check(Path(path), None) is None


def test_blocks_file_containing_a_known_name(tmp_path: Path) -> None:
    file = tmp_path / "notes.md"
    file.write_text("Läsnä: MAIJA MEIKÄLÄINEN (pj)", encoding="utf-8")
    assert "known member name" in guard.check(file, PATTERN)


def test_name_must_match_whole_words(tmp_path: Path) -> None:
    file = tmp_path / "notes.md"
    file.write_text("Teppo Testaajakin ei ole sama nimi", encoding="utf-8")
    assert guard.check(file, PATTERN) is None


def test_binary_files_are_not_scanned(tmp_path: Path) -> None:
    file = tmp_path / "image.png"
    file.write_bytes(b"\x89PNG\xff\xfe\x00")
    assert guard.check(file, PATTERN) is None
