from __future__ import annotations

import argparse
import os

import uvicorn

from server_tg_home.core.config import load_settings
from server_tg_home.core.logging import configure_logging
from server_tg_home.database.session import init_db
from server_tg_home.media.storage import ensure_storage
from server_tg_home.workers.buffer import BufferWorker
from server_tg_home.workers.retention import RetentionWorker


def main() -> None:
    parser = argparse.ArgumentParser(prog="server-tg-home")
    parser.add_argument("--config", help="Path to YAML config file")
    subparsers = parser.add_subparsers(dest="command", required=True)

    api = subparsers.add_parser("api", help="Run HTTP API and Telegram polling")
    api.add_argument("--host", default="0.0.0.0")
    api.add_argument("--port", type=int, default=8080)
    api.add_argument("--reload", action="store_true")

    subparsers.add_parser("worker", help="Run job worker")
    subparsers.add_parser("buffer", help="Run RTSP buffer worker")

    retention = subparsers.add_parser("retention", help="Run storage retention worker")
    retention.add_argument("--once", action="store_true", help="Run one retention check and exit")

    subparsers.add_parser("init-db", help="Initialize database and storage folders")

    args = parser.parse_args()
    if args.config:
        os.environ["STH_CONFIG"] = args.config

    if args.command == "api":
        uvicorn.run(
            "server_tg_home.api.app:create_app",
            factory=True,
            host=args.host,
            port=args.port,
            reload=args.reload,
        )
        return

    settings = load_settings()
    configure_logging(settings.app.log_level)
    ensure_storage(settings)

    if args.command == "worker":
        init_db(settings.app.database_url)
        os.execvp(
            "dramatiq",
            [
                "dramatiq",
                "--processes",
                "1",
                "--threads",
                "1",
                "server_tg_home.jobs.tasks",
            ],
        )
    elif args.command == "buffer":
        BufferWorker(settings).run_forever()
    elif args.command == "retention":
        init_db(settings.app.database_url)
        RetentionWorker(settings).run_forever(once=args.once)
    elif args.command == "init-db":
        init_db(settings.app.database_url)
        return
