#!/usr/bin/env python3
"""Pre-commit guard: keep real meeting content out of the (public) repository.

Rejects staged files that
  - are under data/,
  - are documents (.pdf, .doc, …) outside a tests/fixtures/ directory,
  - are .env files (other than .env.example),
  - contain a name listed in data/guard/names.txt (one name per line, # for comments).

Usage (via pre-commit): guard_commit.py FILE...
"""

import re
import sys
from pathlib import Path

DOCUMENT_SUFFIXES = {".pdf", ".doc", ".docx", ".rtf", ".odt"}
NAMES_FILE = Path("data/guard/names.txt")


def load_names() -> list[str]:
    if not NAMES_FILE.exists():
        return []
    lines = NAMES_FILE.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def names_pattern(names: list[str]) -> re.Pattern[str] | None:
    if not names:
        return None
    alternatives = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)


def check(path: Path, pattern: re.Pattern[str] | None) -> str | None:
    parts = path.parts
    if parts and parts[0] == "data":
        return "files under data/ must never be committed"
    if path.suffix.lower() in DOCUMENT_SUFFIXES and "fixtures" not in parts:
        return "document files are only allowed in tests/fixtures/ (synthetic content)"
    if path.name.startswith(".env") and path.name != ".env.example":
        return ".env files contain local secrets"
    if pattern is not None and path.is_file():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return None  # binary file, nothing to scan
        if match := pattern.search(str(path)) or pattern.search(text):
            return f"contains a known member name ({match.group(0)!r})"
    return None


def main(argv: list[str]) -> int:
    names = load_names()
    if not names:
        print(f"guard: {NAMES_FILE} missing or empty, skipping name check", file=sys.stderr)
    pattern = names_pattern(names)

    failures = [(f, reason) for f in argv if (reason := check(Path(f), pattern))]
    for f, reason in failures:
        print(f"BLOCKED {f}: {reason}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
