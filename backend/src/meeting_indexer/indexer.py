"""The indexing pipeline: file → text → LLM extraction → validation → database.

Reading and LLM extraction ("analysis") run in a worker process with a hard time limit, so one document
can never hold up a run. Every outcome is recorded per document; nothing a run skips goes unrecorded.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal

import psycopg

from meeting_indexer import db
from meeting_indexer.extract import extract_pages, has_text, relative_path, sha256
from meeting_indexer.limits import TimeLimitExceeded, run_with_time_limit
from meeting_indexer.llm import EXTRACTOR_VERSION, Extractor, Meeting
from meeting_indexer.normalize import (
    Warnings,
    chunk_pages,
    clean_name,
    clean_role,
    clean_title,
    meeting_date,
    parse_time,
    split_item_number,
    topic_pages,
)
from meeting_indexer.people import resolve_person

log = logging.getLogger(__name__)


@dataclass
class Analysis:
    pages: list[str]
    meeting: Meeting | None  # None: no text layer, the LLM wasn't called


def analyze_document(path: Path, ollama_host: str, model: str) -> Analysis:
    """Text extraction and LLM extraction for one document. Runs in the worker process."""
    pages = extract_pages(path)
    if not has_text(pages):
        return Analysis(pages, None)
    return Analysis(pages, Extractor(ollama_host, model)(pages))


AnalyzeFn = Callable[[Path], Analysis]


class TimeLimitedAnalyzer:
    """analyze_document in a worker process that is killed if it exceeds the time limit."""

    def __init__(self, ollama_host: str, model: str, seconds: float) -> None:
        self.ollama_host, self.model, self.seconds = ollama_host, model, seconds

    def __call__(self, path: Path) -> Analysis:
        return run_with_time_limit(analyze_document, path, self.ollama_host, self.model, seconds=self.seconds)


# What happened to a document in a run: a document status, or skipped because nothing changed.
ResultStatus = db.DocumentStatus | Literal["skipped"]


@dataclass
class Result:
    rel_path: str
    status: ResultStatus
    seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


class RunStopped(Exception):
    """The run was stopped because of a problem that isn't about any single document."""

    def __init__(self, reason: str, results: list[Result]) -> None:
        super().__init__(reason)
        self.results = results


def should_skip(stored: db.StoredDocument | None, digest: str, model: str, retry_failed: bool) -> bool:
    if stored is None or stored.sha256 != digest or stored.status == "pending":
        return False
    if stored.status in db.FAILED:
        # Retrying automatically would spend the full time limit on the same file on every run.
        return not retry_failed
    if stored.status == "no_text":
        return True
    return stored.extractor_version == EXTRACTOR_VERSION and stored.llm_model == model


def index_file(
    conn: psycopg.Connection,
    path: Path,
    root: Path,
    analyze: AnalyzeFn,
    model: str,
    *,
    force: bool = False,
    retry_failed: bool = False,
    today: date | None = None,
) -> Result:
    rel_path = relative_path(path, root)
    file_type = path.suffix.lower().lstrip(".")
    started = time.monotonic()
    digest: str | None = None
    attempt_started = False

    try:
        digest = sha256(path)
        if not force and should_skip(db.get_document(conn, rel_path), digest, model, retry_failed):
            return Result(rel_path, "skipped")
        db.start_attempt(conn, rel_path, file_type)
        attempt_started = True

        analysis = analyze(path)  # the slow part; no transaction is held while it runs
        seconds = time.monotonic() - started
        if analysis.meeting is None:
            with conn.transaction():
                db.save_document(
                    conn,
                    rel_path=rel_path,
                    sha256=digest,
                    file_type=file_type,
                    pages=analysis.pages,
                    status="no_text",
                    duration_seconds=seconds,
                )
            return Result(rel_path, "no_text", seconds)

        warnings = Warnings()
        with conn.transaction():
            store(
                conn, rel_path, digest, file_type, analysis, model, warnings, seconds, today or date.today()
            )
        return Result(rel_path, "indexed", seconds, warnings.items)

    except TimeLimitExceeded as e:
        log.warning("%s timed out after %.0f s: %s", rel_path, time.monotonic() - started, e)
        return _failed(conn, rel_path, file_type, digest, "timed_out", str(e), started, attempt_started)
    except Exception as e:
        # Errors raised in the worker process carry its traceback as text; others have their own.
        details = getattr(e, "worker_traceback", None)
        log.error(
            "%s failed: %s: %s%s",
            rel_path,
            type(e).__name__,
            e,
            f"\n{details}" if details else "",
            exc_info=None if details else e,
        )
        error = f"{type(e).__name__}: {e}"
        return _failed(conn, rel_path, file_type, digest, "failed", error, started, attempt_started)


def _failed(
    conn: psycopg.Connection,
    rel_path: str,
    file_type: str,
    digest: str | None,
    status: db.DocumentStatus,
    error: str,
    started: float,
    attempt_started: bool,
) -> Result:
    seconds = time.monotonic() - started
    if not attempt_started:  # failed before the attempt was counted, e.g. the file couldn't be read
        db.start_attempt(conn, rel_path, file_type)
    db.record_failure(
        conn,
        rel_path=rel_path,
        file_type=file_type,
        sha256=digest,
        status=status,
        error=error,
        duration_seconds=seconds,
    )
    return Result(rel_path, status, seconds, error=error)


def store(
    conn: psycopg.Connection,
    rel_path: str,
    digest: str,
    file_type: str,
    analysis: Analysis,
    model: str,
    warnings: Warnings,
    seconds: float,
    today: date,
) -> None:
    pages, meeting = analysis.pages, analysis.meeting
    assert meeting is not None
    document_id = db.save_document(
        conn,
        rel_path=rel_path,
        sha256=digest,
        file_type=file_type,
        pages=pages,
        status="indexed",
        duration_seconds=seconds,
        extractor_version=EXTRACTOR_VERSION,
        llm_model=model,
    )
    fields = {
        "title": clean_title(meeting.title),
        "meeting_type": meeting.meeting_type,
        "meeting_date": meeting_date(meeting.date, pages[0], warnings, today),
        "start_time": parse_time(meeting.start_time),
        "end_time": parse_time(meeting.end_time),
        "location": meeting.location,
        "summary": meeting.summary,
    }
    numbered = [split_item_number(t.title) for t in meeting.topics]
    page_numbers = topic_pages([title for _, title in numbered], pages, warnings)
    meeting_id = db.insert_meeting(conn, document_id, fields)

    for status, persons in (("present", meeting.present), ("absent", meeting.absent)):
        for person in persons:
            name = clean_name(person.name)
            if not name:
                continue
            person_id = resolve_person(conn, name)
            if not db.insert_attendance(conn, meeting_id, person_id, status, clean_role(person.role), name):
                warnings.add(f"{name!r} is listed more than once in the attendance")

    db.insert_topics(
        conn,
        meeting_id,
        [
            {
                "ordinal": i,
                "item_number": item_number,
                "title": title,
                "summary": topic.summary,
                "decisions": topic.decisions,
                "page_no": page_no,
            }
            for i, (topic, (item_number, title), page_no) in enumerate(
                zip(meeting.topics, numbered, page_numbers, strict=True)
            )
        ],
    )
    db.insert_chunks(conn, document_id, chunk_pages(pages))
    db.set_raw_extraction(conn, meeting_id, {"llm": meeting.model_dump(), "warnings": warnings.items})


def index_paths(
    conn: psycopg.Connection,
    paths: list[Path],
    root: Path,
    analyze: AnalyzeFn,
    model: str,
    *,
    force: bool = False,
    retry_failed: bool = False,
    max_consecutive_failures: int = 3,
    service_available: Callable[[], bool] | None = None,
    on_result: Callable[[int, int, Result], None] | None = None,
) -> list[Result]:
    """Index each path. A failing document is recorded and the run continues.

    The run stops (RunStopped) only for problems that aren't about one document: the LLM service not
    responding, or several documents in a row failing, which points at the system, not the files.
    Documents it doesn't reach stay registered as pending, so `mi status` shows them.
    """
    db.register_pending(conn, [(relative_path(p, root), p.suffix.lower()[1:]) for p in paths])
    results: list[Result] = []
    consecutive_failures = 0
    for i, path in enumerate(paths, 1):
        result = index_file(conn, path, root, analyze, model, force=force, retry_failed=retry_failed)
        results.append(result)
        if on_result:
            on_result(i, len(paths), result)

        if result.status in db.COMPLETED:
            consecutive_failures = 0
        elif result.status in db.FAILED:
            consecutive_failures += 1
            if service_available is not None and not service_available():
                raise RunStopped("the LLM service is not responding", results)
            if consecutive_failures >= max_consecutive_failures:
                raise RunStopped(f"{consecutive_failures} documents in a row failed", results)
    return results
