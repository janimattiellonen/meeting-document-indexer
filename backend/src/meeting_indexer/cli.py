"""Command-line interface: `mi …`."""

import ollama
import typer

from meeting_indexer import db
from meeting_indexer.config import get_settings
from meeting_indexer.extract import discover

app = typer.Typer(help="Index and search meeting minutes. Everything runs locally.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Keeps `mi status` a subcommand while it is the only command."""


def report(ok: bool, label: str, detail: str) -> None:
    mark = typer.style("OK  ", fg="green") if ok else typer.style("FAIL", fg="red")
    typer.echo(f"{mark} {label:<12} {detail}")


def has_model(available: set[str], name: str) -> bool:
    return name in available or f"{name}:latest" in available


@app.command()
def status() -> None:
    """Check the database, Ollama and the documents directory."""
    settings = get_settings()
    healthy = True

    try:
        with db.connect() as conn:
            counts = db.document_counts(conn)
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
        report(
            True, "documents", f"{sum(1 for _ in discover(settings.docs_root))} files in {settings.docs_root}"
        )
    else:
        report(False, "documents", f"{settings.docs_root} does not exist")
        healthy = False

    raise typer.Exit(0 if healthy else 1)
