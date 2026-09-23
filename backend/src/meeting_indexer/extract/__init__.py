"""Finding documents and extracting their text."""

from collections.abc import Iterator
from pathlib import Path

SUPPORTED_SUFFIXES = {".pdf", ".doc", ".docx", ".rtf", ".odt"}


def discover(root: Path) -> Iterator[Path]:
    """Yield supported documents under root, sorted, skipping Office lock files and hidden files."""
    for path in sorted(root.rglob("*")):
        if (
            path.is_file()
            and path.suffix.lower() in SUPPORTED_SUFFIXES
            and not path.name.startswith(("~$", "."))
        ):
            yield path
