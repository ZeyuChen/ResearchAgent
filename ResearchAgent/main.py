from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timedelta

import uvicorn

from research_agent.config import Settings
from research_agent.services.pipeline import ResearchPipeline
from research_agent.web.api import create_app


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ResearchAgent entrypoint")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run the fetch -> read -> store workflow once")
    run_parser.add_argument("--limit", type=int, default=None, help="Only process the first N filtered items")

    serve_parser = subparsers.add_parser("serve", help="Run the FastAPI web server")
    serve_parser.add_argument("--host", default=None)
    serve_parser.add_argument("--port", type=int, default=None)

    schedule_parser = subparsers.add_parser("schedule", help="Run the workflow every day")
    schedule_parser.add_argument("--run-immediately", action="store_true")
    schedule_parser.add_argument("--limit", type=int, default=None)

    return parser.parse_args()


def run_workflow(settings: Settings, limit: int | None = None) -> list[dict]:
    pipeline = ResearchPipeline.from_settings(settings)
    return pipeline.run_once(limit=limit)


def schedule_loop(settings: Settings, run_immediately: bool, limit: int | None = None) -> None:
    logger = logging.getLogger(__name__)
    if run_immediately:
        processed = run_workflow(settings, limit=limit)
        logger.info("Immediate run completed, processed=%s", len(processed))

    while True:
        next_run = _next_scheduled_time(settings.schedule_time)
        wait_seconds = max((next_run - datetime.now()).total_seconds(), 1)
        logger.info("Next run scheduled at %s", next_run.isoformat(timespec="seconds"))
        time.sleep(wait_seconds)
        processed = run_workflow(settings, limit=limit)
        logger.info("Scheduled run completed, processed=%s", len(processed))


def _next_scheduled_time(schedule_time: str) -> datetime:
    hour_str, minute_str = schedule_time.split(":")
    now = datetime.now()
    candidate = now.replace(hour=int(hour_str), minute=int(minute_str), second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def main() -> None:
    configure_logging()
    args = parse_args()
    settings = Settings.from_env()

    if args.command == "run":
        processed = run_workflow(settings, limit=args.limit)
        logging.getLogger(__name__).info("Run completed, processed=%s", len(processed))
        return

    if args.command == "serve":
        app = create_app(settings)
        uvicorn.run(
            app,
            host=args.host or settings.host,
            port=args.port or settings.port,
            reload=False,
        )
        return

    if args.command == "schedule":
        schedule_loop(settings, run_immediately=args.run_immediately, limit=args.limit)
        return

    raise SystemExit(1)


if __name__ == "__main__":
    main()
