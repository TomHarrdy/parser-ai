"""
Brand Mentions AI Agent — entrypoint.

Usage:
  python -m src.main run          Run pipeline once
  python -m src.main daemon       Run as daemon with APScheduler
  python -m src.main digest       Generate and send weekly digest (once)
"""

import asyncio
import logging
import sys
from datetime import datetime

from src.settings import get_settings
from src.db import init_db

logger = logging.getLogger(__name__)


async def _run_once() -> None:
    """Run the monitoring pipeline once."""
    settings = get_settings()
    await init_db(settings)

    from src.agent import run_pipeline

    result = await run_pipeline(settings)

    if result.errors:
        logger.warning("Pipeline completed with %d errors", len(result.errors))
        for e in result.errors:
            logger.warning("  - %s", e)


async def _run_daemon() -> None:
    """Run as a daemon with periodic scheduling."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    settings = get_settings()
    await init_db(settings)

    scheduler = AsyncIOScheduler()

    # Add the monitoring job — next_run_time=datetime.now() triggers immediately on start
    scheduler.add_job(
        _run_once,
        "interval",
        minutes=settings.schedule_interval_minutes,
        id="monitor",
        name="Brand mention monitor",
        next_run_time=datetime.now(),
    )

    # Add weekly digest job: Monday at 9:00
    scheduler.add_job(
        _send_digest,
        "cron",
        day_of_week="mon",
        hour=9,
        minute=0,
        id="weekly_digest",
        name="Weekly digest",
    )

    scheduler.start()
    logger.info(
        "Daemon started: monitor every %d min, digest every Monday 09:00",
        settings.schedule_interval_minutes,
    )

    try:
        await asyncio.Event().wait()  # run forever
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        logger.info("Daemon stopped")


async def _send_digest() -> None:
    """Generate and send weekly digest."""
    from src.digest import send_weekly_digest
    await send_weekly_digest()


async def main() -> None:
    from src.logging_config import setup_logging

    settings = get_settings()
    setup_logging(settings.log_level)

    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"

    if cmd == "run":
        await _run_once()
    elif cmd == "daemon":
        await _run_daemon()
    elif cmd == "digest":
        await _send_digest()
    else:
        print(f"Usage: python -m src.main [run|daemon|digest]")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
