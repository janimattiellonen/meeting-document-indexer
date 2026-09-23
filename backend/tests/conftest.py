"""Shared fixtures. Database tests run against a throwaway database created from the migrations.

They need the compose database (`docker compose up -d`) and are skipped if it isn't running.
All names and content in tests are invented.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from meeting_indexer.config import REPO_ROOT, get_settings

MIGRATIONS = REPO_ROOT / "db" / "migrations"


def up_section(migration: Path) -> str:
    text = migration.read_text(encoding="utf-8")
    return text.split("-- migrate:up", 1)[1].split("-- migrate:down", 1)[0]


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    try:
        admin = psycopg.connect(get_settings().database_url, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("database not running (docker compose up -d)")
    name = f"meeting_indexer_test_{os.getpid()}"
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = make_conninfo(get_settings().database_url, dbname=name)
    try:
        with psycopg.connect(url, autocommit=True) as conn:
            for migration in sorted(MIGRATIONS.glob("*.sql")):
                conn.execute(up_section(migration).encode())
        yield url
    finally:
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        admin.close()


@pytest.fixture
def conn(database_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(database_url, autocommit=True) as connection:
        yield connection
        connection.execute("TRUNCATE documents, people RESTART IDENTITY CASCADE")
