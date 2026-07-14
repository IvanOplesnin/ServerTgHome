from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def upgrade_database(database_url: str) -> None:
    os.environ["STH_DATABASE_URL"] = database_url
    config = Config(str(_find_alembic_ini()))
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
