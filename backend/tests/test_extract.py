from pathlib import Path

from meeting_indexer.extract import discover


def test_discover_finds_supported_files_recursively(tmp_path: Path) -> None:
    for name in ["2019/a.pdf", "2019/b.DOC", "c.docx", "notes.txt", "~$lock.docx", ".hidden.pdf"]:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).touch()

    found = [p.relative_to(tmp_path).as_posix() for p in discover(tmp_path)]

    assert found == ["2019/a.pdf", "2019/b.DOC", "c.docx"]
