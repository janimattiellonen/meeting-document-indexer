"""Settings, read from the environment and the repository-level .env file."""

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]

# Hosts that mean "this machine". host.docker.internal is the Mac host as seen from a container.
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

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

    @field_validator("ollama_host", "database_url")
    @classmethod
    def must_be_local(cls, value: str) -> str:
        # Document content is sent to both of these; it must never leave this machine.
        host = urlparse(value if "://" in value else f"http://{value}").hostname
        if host not in LOCAL_HOSTS:
            raise ValueError(f"must point to this machine, got host {host!r}")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
