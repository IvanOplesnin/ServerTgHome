from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

POSTGRES_MIGRATION_LOCK_KEY = (793337, 1)


def upgrade_database(database_url: str) -> None:
    os.environ["STH_DATABASE_URL"] = database_url
    config = Config(str(_find_alembic_ini()))
    with _migration_lock(database_url):
        if _should_stamp_existing_initial_schema(database_url):
            command.stamp(config, "0001_initial")
        command.upgrade(config, "head")


def _find_alembic_ini() -> Path:
    candidates = [
        Path.cwd() / "alembic.ini",
        Path(__file__).resolve().parents[2] / "alembic.ini",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("Could not find alembic.ini")


def _should_stamp_existing_initial_schema(database_url: str) -> bool:
    engine = create_engine(database_url, future=True)
    try:
        inspector = inspect(engine)
        has_existing_schema = inspector.has_table("jobs") and inspector.has_table("videos")
        has_alembic_version = inspector.has_table("alembic_version")
        return has_existing_schema and not has_alembic_version
    finally:
        engine.dispose()


@contextmanager
def _migration_lock(database_url: str) -> Iterator[None]:
    engine = create_engine(database_url, future=True)
    if engine.dialect.name != "postgresql":
        engine.dispose()
        yield
        return

    lock_a, lock_b = POSTGRES_MIGRATION_LOCK_KEY
    try:
        with engine.connect() as connection:
            connection.execute(text("select pg_advisory_lock(:lock_a, :lock_b)"), {"lock_a": lock_a, "lock_b": lock_b})
            try:
                yield
            finally:
                connection.execute(
                    text("select pg_advisory_unlock(:lock_a, :lock_b)"),
                    {"lock_a": lock_a, "lock_b": lock_b},
                )
    finally:
        engine.dispose()
