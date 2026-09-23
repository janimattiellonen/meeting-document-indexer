"""Command-line interface: `mi …`."""

import json
import logging
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Annotated

import ollama
import typer

from meeting_indexer import db, evaluation
from meeting_indexer.config import REPO_ROOT, get_settings
from meeting_indexer.extract import SUPPORTED_SUFFIXES, discover, normalize_path, relative_path, sha256
from meeting_indexer.indexer import Result, ResultStatus, RunStopped, TimeLimitedAnalyzer, index_paths

app = typer.Typer(help="Index and search meeting minutes. Everything runs locally.", no_args_is_help=True)

EVAL_DIR = REPO_ROOT / "data" / "eval"
LOG_DIR = REPO_ROOT / "data" / "logs"

Paths = Annotated[
    list[Path] | None,
    typer.Argument(help="Files or directories under DOCS_ROOT. Default: all of DOCS_ROOT."),
]
DocumentPath = Annotated[Path, typer.Argument(help="Document path, or its path relative to DOCS_ROOT")]


# Written to the run's log file; on the console `mi index` prints its own lines instead.
FILE_ONLY_LOGGERS = ("meeting_indexer.indexer", "meeting_indexer.run")


class HideFileOnlyLogs(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith(FILE_ONLY_LOGGERS)


@app.callback()
def main(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show detailed logging")] = False,
) -> None:
    console = logging.StreamHandler()
    console.setLevel(logging.INFO if verbose else logging.WARNING)
    console.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    if not verbose:
        console.addFilter(HideFileOnlyLogs())
    logging.basicConfig(level=logging.WARNING, handlers=[console])


def report(ok: bool, label: str, detail: str) -> None:
    mark = typer.style("OK  ", fg="green") if ok else typer.style("FAIL", fg="red")
    typer.echo(f"{mark} {label:<12} {detail}")


def has_model(available: set[str], name: str) -> bool:
    return name in available or f"{name}:latest" in available


def ollama_responds(host: str) -> bool:
    try:
        ollama.Client(host=host, timeout=10).list()
        return True
    except Exception:
        return False


def format_duration(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m {secs:02d}s"


def print_section(title: str, lines: list[str]) -> None:
    if lines:
        typer.echo(f"\n{title} ({len(lines)}):")
        for line in lines:
            typer.echo(f"  {line}")


def changed_files(
    stored: Mapping[str, tuple[str | None, db.DocumentStatus]], on_disk: Mapping[str, Path]
) -> tuple[list[str], list[str]]:
    """(changed, unreadable): registered files whose content differs from the version last processed,
    whatever the outcome was (indexed, no text, failed or timed out), and "path: error" for files that
    couldn't be read to compare. Pending files have no hash yet."""
    changed: list[str] = []
    unreadable: list[str] = []
    for rel, (digest, _) in sorted(stored.items()):
        if digest is None or rel not in on_disk:
            continue
        try:
            if sha256(on_disk[rel]) != digest:
                changed.append(rel)
        except OSError as e:  # one unreadable file must not hide the rest of the status
            unreadable.append(f"{rel}: {type(e).__name__}: {e.strerror or e}")
    return changed, unreadable


@app.command()
def status() -> None:
    """Check the setup, and list every document that isn't indexed or has changed since."""
    settings = get_settings()
    healthy = True

    try:
        with db.connect() as conn:
            counts = db.document_counts(conn)
            problems = db.problem_documents(conn)
            stored = db.stored_states(conn)
        summary = ", ".join(f"{s}: {n}" for s, n in sorted(counts.items())) or "no documents indexed yet"
        report(True, "database", summary)
    except Exception as e:
        report(False, "database", f"{e}".strip().splitlines()[0])
        raise typer.Exit(1) from None

    try:
        models = ollama.Client(host=settings.ollama_host, timeout=10).list().models
        available = {m.model for m in models if m.model}
        report(True, "ollama", settings.ollama_host)
        for role, name in (("llm", settings.llm_model), ("embeddings", settings.embed_model)):
            found = has_model(available, name)
            report(found, role, name if found else f"{name} not found, run `ollama pull {name}`")
            healthy &= found
    except Exception as e:
        report(False, "ollama", f"{settings.ollama_host}: {e}")
        healthy = False

    if not settings.docs_root.is_dir():
        report(False, "documents", f"{settings.docs_root} does not exist")
        raise typer.Exit(1)
    on_disk = {relative_path(p, settings.docs_root): p for p in discover(settings.docs_root)}
    report(True, "documents", f"{len(on_disk)} files in {settings.docs_root}")

    def attempt(p: db.ProblemDocument) -> str:
        when = p.last_attempt_at.astimezone().strftime("%Y-%m-%d %H:%M") if p.last_attempt_at else "never"
        took = f", took {format_duration(p.duration_seconds)}" if p.duration_seconds else ""
        return f"{p.attempts} attempt(s), last {when}{took}"

    by_status: dict[db.DocumentStatus, list[db.ProblemDocument]] = defaultdict(list)
    for p in problems:
        by_status[p.status].append(p)
    print_section(
        "Never processed (pending): a run was stopped or hasn't reached them",
        [p.rel_path for p in by_status["pending"]],
    )
    print_section(
        "Timed out (retry with `mi index --retry-failed`)",
        [f"{p.rel_path}: {attempt(p)}" for p in by_status["timed_out"]],
    )
    print_section(
        "Failed (retry with `mi index --retry-failed`)",
        [f"{p.rel_path}: {p.error} ({attempt(p)})" for p in by_status["failed"]],
    )
    print_section("No text layer, probably scanned (needs OCR)", [p.rel_path for p in by_status["no_text"]])
    print_section("Not registered yet (added after the last run)", sorted(set(on_disk) - set(stored)))
    changed, unreadable = changed_files(stored, on_disk)
    print_section("Changed since last processed (re-extracted on the next run)", changed)
    print_section("Can't be read, so not compared with the database", unreadable)
    print_section("In the database but no longer on disk", sorted(set(stored) - set(on_disk)))

    raise typer.Exit(0 if healthy else 1)


def resolve_targets(paths: list[Path] | None, root: Path) -> list[Path]:
    """The documents to index. A relative path that doesn't exist as given is taken relative to root.

    A path that doesn't exist or isn't a supported document is refused here, so a typo never gets
    registered and shown in `mi status` as a failed document.
    """
    if not paths:
        return list(discover(root))
    files: list[Path] = []
    for given in paths:
        path = (given if given.exists() or given.is_absolute() else root / given).resolve()
        if not path.is_relative_to(root):
            raise typer.BadParameter(f"{path} is not under DOCS_ROOT ({root})")
        if not path.exists():
            raise typer.BadParameter(f"{given} does not exist (in DOCS_ROOT {root} either)")
        if path.is_file() and path.suffix.lower() not in SUPPORTED_SUFFIXES:
            supported = ", ".join(sorted(SUPPORTED_SUFFIXES))
            raise typer.BadParameter(f"{given} is not a supported document ({supported})")
        files.extend(discover(path) if path.is_dir() else [path])
    return files


def rel_path_of(path: Path) -> str:
    """Accepts a path to an existing file, or a path already relative to DOCS_ROOT."""
    if path.exists():
        return relative_path(path, get_settings().docs_root)
    return normalize_path(path.as_posix())


def start_run_log() -> Path:
    """A log file for this run in data/logs/, with every document's outcome and full error details."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"index-{datetime.now():%Y%m%d-%H%M%S}.log"
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger("meeting_indexer").setLevel(logging.INFO)
    return path


STATUS_COLORS: dict[ResultStatus, str] = {
    "indexed": "green",
    "skipped": "bright_black",
    "no_text": "yellow",
    "failed": "red",
    "timed_out": "red",
}


def run_index(paths: list[Path] | None, force: bool, retry_failed: bool, time_limit: int | None) -> None:
    settings = get_settings()
    files = resolve_targets(paths, settings.docs_root)
    if not files:
        typer.echo(f"No supported documents found in {settings.docs_root}")
        raise typer.Exit(1)

    limit = time_limit or settings.doc_time_limit_seconds
    log_path = start_run_log()
    log = logging.getLogger("meeting_indexer.run")
    log.info("run started: %d files, time limit %d s per document", len(files), limit)
    typer.echo(f"{len(files)} files, time limit {format_duration(limit)} per document. Log: {log_path}\n")

    analyzer = TimeLimitedAnalyzer(settings.ollama_host, settings.llm_model, limit)
    processed_seconds: list[float] = []

    def on_result(i: int, total: int, result: Result) -> None:
        log.info(
            "[%d/%d] %s %s %.0fs %s",
            i,
            total,
            result.status,
            result.rel_path,
            result.seconds,
            result.error or "",
        )
        status = typer.style(f"{result.status:<9}", fg=STATUS_COLORS[result.status])
        line = f"[{i}/{total}] {status} {result.rel_path}"
        if result.status != "skipped":
            processed_seconds.append(result.seconds)
            line += f"  {result.seconds:.0f}s"
            if i < total:
                remaining = (total - i) * sum(processed_seconds) / len(processed_seconds)
                line += f", at most ~{format_duration(remaining)} left"
        typer.echo(line)
        for warning in result.warnings:
            typer.echo(typer.style(f"      ! {warning}", fg="yellow"))
        if result.error:
            typer.echo(typer.style(f"      {result.error}", fg="red"))

    stopped: RunStopped | None = None
    with db.connect() as conn:
        try:
            results = index_paths(
                conn,
                files,
                settings.docs_root,
                analyzer,
                settings.llm_model,
                force=force,
                retry_failed=retry_failed,
                max_consecutive_failures=settings.max_consecutive_failures,
                service_available=lambda: ollama_responds(settings.ollama_host),
                on_result=on_result,
            )
        except RunStopped as e:
            stopped, results = e, e.results

    totals = Counter(r.status for r in results)
    summary = ", ".join(f"{s}: {totals[s]}" for s in STATUS_COLORS)
    log.info("run finished: %s", summary)
    typer.echo("\n" + summary)
    if stopped:
        not_reached = files[len(results) :]
        log.error("run stopped: %s; %d documents not reached:", stopped, len(not_reached))
        for path in not_reached:
            log.error("  not reached: %s", relative_path(path, settings.docs_root))
        typer.echo(
            typer.style(f"\nRun stopped: {stopped}.", fg="red")
            + f" {len(not_reached)} documents were not reached (listed in the log). New ones are pending"
            " in `mi status`; the others keep their earlier result."
        )
        raise typer.Exit(2)
    if totals["failed"] or totals["timed_out"]:
        typer.echo("See `mi status` for the documents that failed or timed out, and the log for details.")
        raise typer.Exit(1)


RetryFailed = Annotated[
    bool,
    typer.Option(help="Also retry documents that failed or timed out before (they're skipped otherwise)"),
]
TimeLimit = Annotated[
    int | None,
    typer.Option(help="Seconds allowed per document (default DOC_TIME_LIMIT_SECONDS, 600)", min=10),
]


@app.command()
def index(
    paths: Paths = None,
    force: Annotated[bool, typer.Option(help="Re-extract even if the file hasn't changed")] = False,
    retry_failed: RetryFailed = False,
    time_limit: TimeLimit = None,
) -> None:
    """Extract meeting data from new and changed documents into the database."""
    run_index(paths, force, retry_failed, time_limit)


@app.command()
def reindex(
    paths: Annotated[list[Path], typer.Argument(help="Files or directories under DOCS_ROOT")],
    time_limit: TimeLimit = None,
) -> None:
    """Re-extract the given documents even if they haven't changed."""
    run_index(paths, force=True, retry_failed=True, time_limit=time_limit)


@app.command()
def show(path: DocumentPath) -> None:
    """Print what was extracted from a document."""
    rel_path = rel_path_of(path)
    with db.connect() as conn:
        meeting = db.load_meeting(conn, rel_path)
    if meeting is None:
        typer.echo(f"No extraction for {rel_path}. Has it been indexed?")
        raise typer.Exit(1)

    def people(status: str) -> str:
        attendees = [a for a in meeting.attendees if a.status == status]
        return ", ".join(f"{a.name} ({a.role})" if a.role else a.name for a in attendees) or "-"

    time_range = "-".join(t.strftime("%H:%M") for t in (meeting.start_time, meeting.end_time) if t)
    typer.echo(typer.style(meeting.title, bold=True) + f"  [{meeting.meeting_type}]")
    typer.echo(f"Tiedosto:   {rel_path}")
    typer.echo(f"Aika:       {meeting.meeting_date or '?'} {time_range}".rstrip())
    typer.echo(f"Paikka:     {meeting.location or '-'}")
    typer.echo(f"Läsnä:      {people('present')}")
    typer.echo(f"Poissa:     {people('absent')}")
    typer.echo("Asiakohdat:")
    for topic in meeting.topics:
        page = f" (s. {topic.page_no})" if topic.page_no else ""
        typer.echo(f"  {topic.item_number or '-':>3}. {topic.title}{page}")
        typer.echo(f"       {topic.summary}")
        if topic.decisions:
            typer.echo(typer.style(f"       Päätös: {topic.decisions}", fg="cyan"))
    typer.echo(f"Yhteenveto: {meeting.summary}")
    for warning in meeting.warnings:
        typer.echo(typer.style(f"! {warning}", fg="yellow"))


def percent(value: float) -> str:
    return f"{value * 100:.0f}%"


def write_golden_draft(path: Path) -> None:
    rel_path = rel_path_of(path)
    with db.connect() as conn:
        meeting = db.load_meeting(conn, rel_path)
    if meeting is None:
        typer.echo(f"No extraction for {rel_path}. Index it first.")
        raise typer.Exit(1)
    target = EVAL_DIR / (Path(rel_path).stem + ".json")
    if target.exists():
        typer.echo(f"{target} already exists; not overwriting.")
        raise typer.Exit(1)
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    draft = evaluation.golden_draft(meeting, rel_path)
    target.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    typer.echo(f"Wrote {target}. Check every field against the document and correct it by hand.")


@app.command("eval")
def eval_command(
    init: Annotated[
        Path | None,
        typer.Option(help="Write a golden draft for this document from its current extraction"),
    ] = None,
) -> None:
    """Compare extractions against the hand-checked golden files in data/eval/."""
    if init is not None:
        write_golden_draft(init)
        return

    with db.connect() as conn:
        scores = evaluation.evaluate(conn, EVAL_DIR)
    if not scores:
        typer.echo(f"No golden files in {EVAL_DIR}. Create one with `mi eval --init <document>`.")
        raise typer.Exit(1)

    for s in scores:
        if s.missing:
            typer.echo(typer.style(f"{s.rel_path}: not indexed", fg="red"))
            continue
        wrong = [name for name, ok in s.checks.items() if not ok]
        typer.echo(
            f"{s.rel_path}: fields {len(s.checks) - len(wrong)}/{len(s.checks)}"
            + (f" (wrong: {', '.join(wrong)})" if wrong else "")
            + f"; present P/R {percent(s.present[0])}/{percent(s.present[1])}"
            + f"; absent P/R {percent(s.absent[0])}/{percent(s.absent[1])}"
            + f"; topics P/R {percent(s.topics[0])}/{percent(s.topics[1])}"
        )

    scored = [s for s in scores if not s.missing]
    if scored:
        fields = sum(sum(s.checks.values()) for s in scored) / sum(len(s.checks) for s in scored)
        recall = sum(s.present[1] + s.absent[1] + s.topics[1] for s in scored) / (3 * len(scored))
        typer.echo(
            f"\n{len(scored)} documents: fields correct {percent(fields)}, "
            f"average recall (people, topics) {percent(recall)}"
        )
