"""Exponential-backoff retry utility for async HTTP calls (httpx)."""

import asyncio
import logging
import random
from typing import Awaitable, Callable, Tuple, TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")

# HTTP status codes worth retrying (rate-limit + server errors)
RETRYABLE_STATUSES: Tuple[int, ...] = (429, 500, 502, 503, 504)


async def with_retry(
    coro_factory: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    label: str = "",
) -> T:
    """Run an async coroutine factory with exponential backoff.

    Retries on:
    - httpx.HTTPStatusError with status in RETRYABLE_STATUSES (429/5xx)
    - httpx.TransportError (network-level failures)

    Non-retryable HTTP errors (4xx except 429) are re-raised immediately.
    """
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await coro_factory()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status not in RETRYABLE_STATUSES or attempt == max_attempts:
                raise
            last_exc = exc
            delay = _backoff(attempt, base_delay, max_delay)
            logger.warning(
                "Retry %d/%d for %s (HTTP %d) - sleeping %.1fs",
                attempt, max_attempts, label or "request", status, delay,
            )
        except httpx.TransportError as exc:
            if attempt == max_attempts:
                raise
            last_exc = exc
            delay = _backoff(attempt, base_delay, max_delay)
            logger.warning(
                "Retry %d/%d for %s (%s) - sleeping %.1fs",
                attempt, max_attempts, label or "request", type(exc).__name__, delay,
            )
        await asyncio.sleep(delay)

    raise last_exc  # type: ignore[misc]


def _backoff(attempt: int, base: float, max_delay: float) -> float:
    """Exponential delay with +-25% jitter to avoid thundering-herd."""
    raw = min(base * (2 ** (attempt - 1)), max_delay)
    return raw * (0.75 + random.random() * 0.5)
