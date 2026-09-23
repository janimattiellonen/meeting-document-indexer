import pytest
from pydantic import ValidationError

from meeting_indexer.config import REPO_ROOT, Settings


def make(**overrides: str) -> Settings:
    return Settings(_env_file=None, **overrides)  # pyright: ignore[reportCallIssue]


def test_defaults_point_to_this_machine() -> None:
    settings = make()
    assert "127.0.0.1" in settings.ollama_host
    assert "127.0.0.1" in settings.database_url


def test_relative_docs_root_is_resolved_against_repo_root() -> None:
    assert make(docs_root="data/documents").docs_root == REPO_ROOT / "data" / "documents"


def test_absolute_docs_root_is_kept() -> None:
    assert str(make(docs_root="/tmp/docs").docs_root) == "/tmp/docs"


@pytest.mark.parametrize(
    "host", ["http://localhost:11434", "http://[::1]:11434", "http://host.docker.internal:11434", "127.0.0.1:11434"]
)
def test_local_ollama_hosts_are_accepted(host: str) -> None:
    assert make(ollama_host=host).ollama_host == host


@pytest.mark.parametrize("host", ["http://192.168.1.10:11434", "https://ollama.example.com", "http://0.0.0.0:11434"])
def test_remote_ollama_hosts_are_rejected(host: str) -> None:
    with pytest.raises(ValidationError, match="must point to this machine"):
        make(ollama_host=host)


def test_remote_database_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must point to this machine"):
        make(database_url="postgresql://u:p@db.example.com:5432/x")
