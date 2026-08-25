"""Command-line entry point for the external task worker."""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import replace
from pathlib import Path
import signal
import threading
from typing import Sequence

from src.server.application import Application
from src.server.config import ServerConfig
from src.services.worker_observability import WorkerObservability

from . import WorkerRunner, WorkerSettings, test_probe_handlers


logger = logging.getLogger("past_partner.worker")


def _build_parser(defaults: ServerConfig) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Past Partner external task worker")
    parser.add_argument(
        "--worker-id",
        default=os.getenv("PAST_PARTNER_WORKER_ID", f"worker-{os.getpid()}"),
    )
    parser.add_argument("--data-dir", type=Path, default=defaults.data_dir)
    parser.add_argument("--web-dir", type=Path, default=defaults.web_dir)
    parser.add_argument("--mode", choices=("development", "test", "production"), default=defaults.mode)
    parser.add_argument("--lease-seconds", type=int, default=60)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--max-tasks", type=int, default=None)
    parser.add_argument(
        "--once",
        action="store_true",
        help="claim at most one task and exit, even when the queue is idle",
    )
    return parser


def _install_stop_handlers(stop_event: threading.Event) -> None:
    def stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)


def main(argv: Sequence[str] | None = None) -> None:
    defaults = ServerConfig.from_env()
    parser = _build_parser(defaults)
    args = parser.parse_args(argv)
    if args.once and args.max_tasks is not None:
        parser.error("--once cannot be combined with --max-tasks")

    settings = WorkerSettings(
        worker_id=args.worker_id,
        lease_seconds=args.lease_seconds,
        poll_seconds=args.poll_seconds,
        max_tasks=args.max_tasks,
    )
    config = replace(
        defaults,
        data_dir=args.data_dir,
        web_dir=args.web_dir,
        mode=args.mode,
    ).validated()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    application = Application.from_config(config)
    stop_event = threading.Event()
    _install_stop_handlers(stop_event)
    runner = WorkerRunner(
        application.task_queue,
        test_probe_handlers(config.mode),
        settings,
        observability=(
            None
            if application.metadata_store is None
            else WorkerObservability(application.metadata_store)
        ),
    )
    try:
        if args.once:
            runner.run_once()
        elif settings.max_tasks is not None:
            runner.run_until_idle()
        else:
            runner.run_forever(stop_event)
        stats = runner.stats.to_dict()
        logger.info(
            "worker stopped worker_id=%s polls=%s claimed=%s idle_polls=%s",
            settings.worker_id,
            stats["polls"],
            stats["claimed"],
            stats["idle_polls"],
        )
    finally:
        application.close()


if __name__ == "__main__":
    main()
