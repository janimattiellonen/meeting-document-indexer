"""Settings, read from the environment and the repository-level .env file."""

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import psycopg
from psycopg.conninfo import conninfo_to_dict
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]

# Hosts that mean "this machine". host.docker.internal is the Mac host as seen from a container.
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}


class Settings(BaseSettings):
    # hide_input_in_errors: a rejected DATABASE_URL may contain the password.
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore", hide_input_in_errors=True)

    database_url: str = "postgresql://meeting_indexer:meeting_indexer@127.0.0.1:5434/meeting_indexer"
    docs_root: Path = Path("data/documents")
    ollama_host: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen3.8:27b-mlx"
    embed_model: str = "bge-m3"
    # Hard limit per document for reading it and the LLM extraction. A normal document takes ~90 s;
    # the LLM output cap (8192 tokens) is reached in ~7 min at ~21 tokens/s.
    doc_time_limit_seconds: int = 600
    # Stop a run after this many documents fail in a row: that points at the system, not the files.
    max_consecutive_failures: int = 3

    @field_validator("docs_root")
    @classmethod
    def resolve_docs_root(cls, value: Path) -> Path:
        return value if value.is_absolute() else (REPO_ROOT / value).resolve()

    # Document content is sent to both of these; they must never point off this machine.
    # The error names only the host: the connection string may contain a password.

    @field_validator("ollama_host")
    @classmethod
    def ollama_must_be_local(cls, value: str) -> str:
        host = urlparse(value if "://" in value else f"http://{value}").hostname
        if host not in LOCAL_HOSTS:
            raise ValueError(f"must point to this machine, got host {host!r}")
        return value

    @field_validator("database_url")
    @classmethod
    def database_must_be_local(cls, value: str) -> str:
        # Accepts both libpq formats: "postgresql://user@host/db" and "host=... dbname=...".
        try:
            hosts = str(conninfo_to_dict(value).get("host") or "localhost").split(",")
        except psycopg.ProgrammingError:
            raise ValueError("is not a valid PostgreSQL connection string") from None
        remote = [h for h in hosts if h not in LOCAL_HOSTS and not h.startswith("/")]  # "/…": a local socket
        if remote:
            raise ValueError(f"must point to this machine, got host {remote[0]!r}")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
