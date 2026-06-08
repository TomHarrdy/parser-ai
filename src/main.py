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


async def _health_server_task(stop_event: asyncio.Event, port: int = 8080) -> None:
    """Minimal async HTTP server for Docker HEALTHCHECK.

    Responds to any TCP request with HTTP 200 {"status":"ok"}.
    Shuts down cleanly when stop_event is set.
    """
    body = b'{"status":"ok"}'
    response = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"Connection: close\r\n"
        b"\r\n"
    ) + body

    async def _handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            await reader.read(1024)  # consume the incoming request
            writer.write(response)
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(_handle, "0.0.0.0", port)
    logger.info("Health-check server listening on port %d", port)
    async with server:
        await stop_event.wait()
    logger.info("Health-check server stopped")


async def _run_daemon() -> None:
    """Run as a daemon with periodic scheduling + Telegram bot polling.

    All three tasks run concurrently via asyncio.gather():
      - Scheduler: fires pipeline every N minutes + weekly digest on Monday 09:00
      - Bot polling: listens for inline button callbacks (e.g. 'Not relevant')
      - Health server: HTTP /health on port 8080 for Docker HEALTHCHECK
    """
    import signal
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from src.bot_handler import start_polling

    settings = get_settings()
    await init_db(settings)

    scheduler = AsyncIOScheduler()

    # Run at the top of every hour (18:00, 19:00, …).
    # next_run_time=datetime.now() ensures one immediate run on startup too.
    scheduler.add_job(
        _run_once,
        "cron",
        minute=0,
        id="monitor",
        name="Brand mention monitor",
        next_run_time=datetime.now(),
    )

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
    logger.info("Daemon started: monitor every hour at :00, digest every Monday 09:00")

    # Graceful shutdown on SIGTERM (Docker stop) and SIGINT (Ctrl+C)
    stop_event = asyncio.Event()

    def _handle_signal() -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    async def _scheduler_task() -> None:
        await stop_event.wait()
        scheduler.shutdown()
        logger.info("Scheduler stopped")

    try:
        # Run scheduler guard + bot polling + health server concurrently.
        await asyncio.gather(
            _scheduler_task(),
            start_polling(settings),
            _health_server_task(stop_event),
            return_exceptions=True,
        )
    finally:
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
