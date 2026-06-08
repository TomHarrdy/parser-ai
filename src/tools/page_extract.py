"""Free-first page extraction cascade.

The agent needs clean page text and a publication/activity date. Firecrawl can
do that, but paid quota failures should not block ordinary pages. This module
tries local extraction first, then Jina Reader, then optional Firecrawl.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional, Tuple

import httpx

from src.tools.firecrawl import (
    _extract_latest_content_date,
    _parse_firecrawl_date,
    scrape_url_with_meta as scrape_url_with_firecrawl_meta,
)
from src.tools.retry import with_retry

logger = logging.getLogger(__name__)

# ── Bug 3 fix: filter known-harmless trafilatura internal noise ───────────────
# trafilatura logs WARNING/ERROR when ZSTD decompression fails or when the
# resulting HTML tree is empty. These are not actionable — the cascade falls
# back to Jina Reader automatically. Suppress them to avoid log spam.

_TRAFILATURA_NOISE_EXACT: frozenset[str] = frozenset(
    [
        "invalid ZSTD file",
        "invalid GZ file",
        "empty HTML tree: None",
        "discarding data: None",
    ]
)
_TRAFILATURA_NOISE_PREFIXES: tuple[str, ...] = (
    "parsed tree length:",
    "wrong data type or not valid HTML",
)


class _TrafilaturaNoiseFilter(logging.Filter):
    """Drop known-harmless trafilatura parse-failure messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if msg in _TRAFILATURA_NOISE_EXACT:
            return False
        if any(msg.startswith(p) for p in _TRAFILATURA_NOISE_PREFIXES):
            return False
        return True


_traf_noise_filter = _TrafilaturaNoiseFilter()
logging.getLogger("trafilatura.utils").addFilter(_traf_noise_filter)
logging.getLogger("trafilatura.core").addFilter(_traf_noise_filter)

# ── Bug 4 fix: pre-initialise the trafilatura urllib3 pool with maxsize > 1 ──
# trafilatura's create_pool() defaults to maxsize=1 per host. When multiple
# asyncio.to_thread() calls run simultaneously for the same domain, urllib3
# discards excess connections with a WARNING. Raising maxsize to 10 eliminates
# the warning without any risk to correctness.

def _init_trafilatura_pool(maxsize: int = 10) -> None:
    """Initialise trafilatura's global urllib3 pool with a larger per-host maxsize."""
    try:
        import trafilatura.downloads as _td
        import certifi

        if _td.HTTP_POOL is None:
            _td.HTTP_POOL = _td.create_pool(
                maxsize=maxsize,
                ca_certs=certifi.where(),
                cert_reqs="CERT_REQUIRED",
            )
            logger.debug("trafilatura urllib3 pool initialised with maxsize=%d", maxsize)
    except Exception as exc:  # pragma: no cover
        logger.debug("Could not pre-init trafilatura pool: %s", exc)


_init_trafilatura_pool()

DEFAULT_JINA_READER_BASE_URL = "https://r.jina.ai"


@dataclass
class ExtractedPage:
    content: Optional[str]
    published_at: Optional[datetime]
    provider: str


def _parse_page_date(raw: object) -> Optional[datetime]:
    if not raw:
        return None
    text = str(raw).strip()
    parsed = _parse_firecrawl_date(text)
    if parsed:
        return parsed
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _looks_blocked_content(title: str, content: str) -> bool:
    combined = f"{title}\n{content}".lower()
    blocked_markers = (
        "checking your browser",
        "verify you are human",
        "are you a robot",
        "проверяем",
        "что вы не робот",
        "captcha",
        "access denied",
    )
    return any(marker in combined for marker in blocked_markers)


def _extract_with_trafilatura_sync(url: str) -> ExtractedPage:
    try:
        import trafilatura
    except ImportError:
        return ExtractedPage(None, None, "trafilatura_missing")

    # Pool is already pre-initialised with maxsize=10 by _init_trafilatura_pool()
    # at module load time (Bug 4 fix). No extra setup needed here.
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return ExtractedPage(None, None, "trafilatura")

    content = trafilatura.extract(
        downloaded,
        include_comments=True,
        include_tables=True,
        output_format="markdown",
    )
    metadata = trafilatura.extract_metadata(downloaded)
    published_at = None
    if metadata is not None:
        published_at = _parse_page_date(getattr(metadata, "date", None))
    if published_at is None and content:
        published_at = _extract_latest_content_date(content)

    return ExtractedPage(content or None, published_at, "trafilatura")


async def _extract_with_trafilatura(url: str) -> ExtractedPage:
    try:
        return await asyncio.to_thread(_extract_with_trafilatura_sync, url)
    except Exception as exc:
        logger.debug("Trafilatura extraction failed for %s: %s", url, exc)
        return ExtractedPage(None, None, "trafilatura")


async def _extract_with_jina_reader(
    url: str,
    base_url: str = DEFAULT_JINA_READER_BASE_URL,
) -> ExtractedPage:
    endpoint = f"{base_url.rstrip('/')}/{url}"

    async def _request() -> ExtractedPage:
        async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
            resp = await client.get(
                endpoint,
                headers={
                    "Accept": "application/json",
                    "X-Respond-With": "frontmatter",
                },
            )
            resp.raise_for_status()
            payload = resp.json()

        data = payload.get("data") or {}
        content = data.get("content") or None
        title = str(data.get("title") or "")
        if content and _looks_blocked_content(title, content):
            return ExtractedPage(None, None, "jina_reader_blocked")

        published_at = _parse_page_date(data.get("publishedTime"))
        if published_at is None:
            metadata = data.get("metadata") or {}
            for key in ("datePublished", "publishedTime", "article:published_time", "date"):
                published_at = _parse_page_date(metadata.get(key))
                if published_at:
                    break
        if published_at is None and content:
            published_at = _extract_latest_content_date(content)

        return ExtractedPage(content, published_at, "jina_reader")

    try:
        return await with_retry(_request, label=f"jina-reader: {url[:60]}")
    except Exception as exc:
        logger.debug("Jina Reader extraction failed for %s: %s", url, exc)
        return ExtractedPage(None, None, "jina_reader")


async def extract_page(
    url: str,
    *,
    firecrawl_api_key: str = "",
    jina_reader_base_url: str = DEFAULT_JINA_READER_BASE_URL,
) -> ExtractedPage:
    """Extract clean page content with a free-first provider cascade."""
    for extractor in (
        _extract_with_trafilatura,
        lambda target: _extract_with_jina_reader(target, jina_reader_base_url),
    ):
        result = await extractor(url)
        if result.content:
            return result

    if firecrawl_api_key:
        try:
            content, published_at = await scrape_url_with_firecrawl_meta(firecrawl_api_key, url)
            if content:
                return ExtractedPage(content, published_at, "firecrawl")
        except Exception as exc:
            logger.warning("Firecrawl fallback failed for %s: %s", url, exc)

    return ExtractedPage(None, None, "none")


async def scrape_url_with_meta(
    api_key: str,
    url: str,
    *,
    jina_reader_base_url: str = DEFAULT_JINA_READER_BASE_URL,
) -> Tuple[Optional[str], Optional[datetime]]:
    """Backward-compatible adapter returning only content and date."""
    result = await extract_page(
        url,
        firecrawl_api_key=api_key,
        jina_reader_base_url=jina_reader_base_url,
    )
    return result.content, result.published_at
