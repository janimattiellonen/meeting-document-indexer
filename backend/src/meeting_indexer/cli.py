"""Command-line interface: `mi …`."""

import json
import logging
from pathlib import Path
from typing import Annotated

import ollama
import typer

from meeting_indexer import db, evaluation
from meeting_indexer.config import REPO_ROOT, get_settings
from meeting_indexer.extract import discover
from meeting_indexer.indexer import Result, index_paths
from meeting_indexer.llm import Extractor

app = typer.Typer(help="Index and search meeting minutes. Everything runs locally.", no_args_is_help=True)

EVAL_DIR = REPO_ROOT / "data" / "eval"

Paths = Annotated[
    list[Path] | None,
    typer.Argument(help="Files or directories under DOCS_ROOT. Default: all of DOCS_ROOT."),
]
DocumentPath = Annotated[Path, typer.Argument(help="Document path, or its path relative to DOCS_ROOT")]


@app.callback()
def main(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show detailed logging")] = False,
) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING, format="%(levelname)s %(message)s"
    )


def report(ok: bool, label: str, detail: str) -> None:
    mark = typer.style("OK  ", fg="green") if ok else typer.style("FAIL", fg="red")
    typer.echo(f"{mark} {label:<12} {detail}")


def has_model(available: set[str], name: str) -> bool:
    return name in available or f"{name}:latest" in available


@app.command()
def status() -> None:
    """Check the database, Ollama and the documents directory, and list problem documents."""
    settings = get_settings()
    healthy = True
    problems: dict[str, list[tuple[str, str | None]]] = {}

    try:
        with db.connect() as conn:
            counts = db.document_counts(conn)
            problems = {s: db.documents_with_status(conn, s) for s in ("failed", "no_text")}
        summary = ", ".join(f"{s}: {n}" for s, n in sorted(counts.items())) or "no documents indexed yet"
        report(True, "database", summary)
    except Exception as e:
        report(False, "database", f"{e}".strip().splitlines()[0])
        healthy = False

    try:
        models = ollama.Client(host=settings.ollama_host).list().models
        available = {m.model for m in models if m.model}
        report(True, "ollama", settings.ollama_host)
        for role, name in (("llm", settings.llm_model), ("embeddings", settings.embed_model)):
            found = has_model(available, name)
            report(found, role, name if found else f"{name} not found, run `ollama pull {name}`")
            healthy &= found
    except Exception as e:
        report(False, "ollama", f"{settings.ollama_host}: {e}")
        healthy = False

    if settings.docs_root.is_dir():
        count = sum(1 for _ in discover(settings.docs_root))
        report(True, "documents", f"{count} files in {settings.docs_root}")
    else:
        report(False, "documents", f"{settings.docs_root} does not exist")
        healthy = False

    if problems.get("failed"):
        typer.echo("\nFailed (retried on the next `mi index`):")
        for rel_path, error in problems["failed"]:
            typer.echo(f"  {rel_path}: {error}")
    if problems.get("no_text"):
        typer.echo("\nNo text layer, probably scanned (needs OCR):")
        for rel_path, _ in problems["no_text"]:
            typer.echo(f"  {rel_path}")

    raise typer.Exit(0 if healthy else 1)


def resolve_targets(paths: list[Path] | None, root: Path) -> list[Path]:
    if not paths:
        return list(discover(root))
    files: list[Path] = []
    for path in paths:
        path = path.resolve()
        if not path.is_relative_to(root):
            raise typer.BadParameter(f"{path} is not under DOCS_ROOT ({root})")
        files.extend(discover(path) if path.is_dir() else [path])
    return files


def rel_path_of(path: Path) -> str:
    """Accepts a path to an existing file, or a path already relative to DOCS_ROOT."""
    if path.exists():
        return path.resolve().relative_to(get_settings().docs_root).as_posix()
    return path.as_posix()


def format_duration(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m {secs:02d}s"


STATUS_COLORS = {"indexed": "green", "skipped": "bright_black", "no_text": "yellow", "failed": "red"}


def run_index(paths: list[Path] | None, force: bool) -> None:
    settings = get_settings()
    files = resolve_targets(paths, settings.docs_root)
    if not files:
        typer.echo(f"No supported documents found in {settings.docs_root}")
        raise typer.Exit(1)

    extractor = Extractor(settings.ollama_host, settings.llm_model)
    llm_seconds: list[float] = []

    def on_result(i: int, total: int, result: Result) -> None:
        status = typer.style(f"{result.status:<8}", fg=STATUS_COLORS[result.status])
        line = f"[{i}/{total}] {status} {result.rel_path}"
        if result.status == "indexed":
            llm_seconds.append(result.seconds)
            line += f"  {result.seconds:.0f}s"
            if i < total:
                remaining = (total - i) * sum(llm_seconds) / len(llm_seconds)
                line += f", at most ~{format_duration(remaining)} left"
        typer.echo(line)
        for warning in result.warnings:
            typer.echo(typer.style(f"      ! {warning}", fg="yellow"))
        if result.error:
            typer.echo(typer.style(f"      {result.error}", fg="red"))

    with db.connect() as conn:
        results = index_paths(
            conn, files, settings.docs_root, extractor, settings.llm_model, force=force, on_result=on_result
        )

    totals = {s: sum(1 for r in results if r.status == s) for s in STATUS_COLORS}
    typer.echo("\n" + ", ".join(f"{s}: {n}" for s, n in totals.items()))
    raise typer.Exit(1 if totals["failed"] else 0)


@app.command()
def index(
    paths: Paths = None,
    force: Annotated[bool, typer.Option(help="Re-extract even if the file hasn't changed")] = False,
) -> None:
    """Extract meeting data from new and changed documents into the database."""
    run_index(paths, force)


@app.command()
def reindex(
    paths: Annotated[list[Path], typer.Argument(help="Files or directories under DOCS_ROOT")],
) -> None:
    """Re-extract the given documents even if they haven't changed."""
    run_index(paths, force=True)


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
