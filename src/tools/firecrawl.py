import re
from html import unescape
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import httpx

from src.tools.retry import with_retry

FIRECRAWL_API_BASE = "https://api.firecrawl.dev"

# Metadata keys where Firecrawl / Open Graph / schema.org expose publication date
_PUB_DATE_KEYS = (
    "publishedTime",
    "article:published_time",
    "og:article:published_time",
    "datePublished",
    "pubdate",
    "date",
)

_RU_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}

_EN_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

_NUMERIC_DATE_RE = re.compile(
    r"(?<!\d)(?P<day>\d{1,2})[./-](?P<month>\d{1,2})[./-](?P<year>20\d{2})"
    r"(?:[ T,]+(?P<hour>\d{1,2}):(?P<minute>\d{2}))?(?!\d)"
)

_RU_DATE_RE = re.compile(
    r"(?<!\d)(?P<day>\d{1,2})\s+"
    r"(?P<month>января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)"
    r"\s+(?P<year>20\d{2})"
    r"(?:\s*(?:г\.?|года)?(?:\s+в)?\s+(?P<hour>\d{1,2}):(?P<minute>\d{2}))?",
    re.IGNORECASE,
)

_EN_DATE_RE = re.compile(
    r"(?<![a-z])(?:on\s+)?"
    r"(?P<month>January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(?P<day>\d{1,2}),\s+(?P<year>20\d{2})"
    r"(?:[ T,]+(?P<hour>\d{1,2}):(?P<minute>\d{2}))?",
    re.IGNORECASE,
)

_META_TAG_RE = re.compile(r"<meta\s+[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r'(?P<key>[a-zA-Z:]+)=["\'](?P<value>[^"\']*)["\']')
_DESCRIPTION_META_NAMES = {"description", "og:description", "twitter:description"}

_TODAY_RE = re.compile(r"(?<![а-яё])сегодня(?![а-яё])", re.IGNORECASE)
_YESTERDAY_RE = re.compile(r"(?<![а-яё])вчера(?![а-яё])", re.IGNORECASE)
_DAYS_AGO_RE = re.compile(
    r"(?<!\d)(?P<days>\d{1,3})\s+д(?:ень|ня|ней)\s+назад",
    re.IGNORECASE,
)
_LONG_AGO_RE = re.compile(r"(?<![а-яё])давно(?![а-яё])", re.IGNORECASE)


def _parse_firecrawl_date(raw: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 / YYYY-MM-DD date string from Firecrawl metadata.

    Returns a UTC-aware datetime, or None if parsing fails.
    """
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw[:25], fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _make_utc_date(
    year: int,
    month: int,
    day: int,
    hour: Optional[str] = None,
    minute: Optional[str] = None,
) -> Optional[datetime]:
    try:
        return datetime(
            year,
            month,
            day,
            int(hour or 0),
            int(minute or 0),
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None


def _extract_explicit_content_dates(content: str) -> list[datetime]:
    dates_with_pos: list[tuple[int, datetime]] = []

    for match in _NUMERIC_DATE_RE.finditer(content):
        dt = _make_utc_date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            match.group("hour"),
            match.group("minute"),
        )
        if dt:
            dates_with_pos.append((match.start(), dt))

    for match in _RU_DATE_RE.finditer(content):
        month = _RU_MONTHS[match.group("month").lower()]
        dt = _make_utc_date(
            int(match.group("year")),
            month,
            int(match.group("day")),
            match.group("hour"),
            match.group("minute"),
        )
        if dt:
            dates_with_pos.append((match.start(), dt))

    for match in _EN_DATE_RE.finditer(content):
        month = _EN_MONTHS[match.group("month").lower()]
        dt = _make_utc_date(
            int(match.group("year")),
            month,
            int(match.group("day")),
            match.group("hour"),
            match.group("minute"),
        )
        if dt:
            dates_with_pos.append((match.start(), dt))

    return [dt for _, dt in sorted(dates_with_pos, key=lambda item: item[0])]


def extract_first_content_date(content: str) -> Optional[datetime]:
    """Extract the first explicit date from page text.

    Instagram pages put the target post date first, then append "More posts"
    recommendations with unrelated newer dates.
    """
    dates = _extract_explicit_content_dates(content)
    return dates[0] if dates else None


def extract_instagram_meta_date(html: str) -> Optional[datetime]:
    """Extract the target post date from Instagram logged-out HTML metadata."""
    for tag in _META_TAG_RE.findall(html):
        attrs = {
            match.group("key").lower(): match.group("value")
            for match in _ATTR_RE.finditer(tag)
        }
        meta_name = attrs.get("name") or attrs.get("property")
        content = attrs.get("content")
        if meta_name not in _DESCRIPTION_META_NAMES or not content:
            continue
        published_at = extract_first_content_date(unescape(content))
        if published_at:
            return published_at
    return None


async def fetch_instagram_meta_date(url: str) -> Optional[datetime]:
    """Fetch Instagram logged-out HTML and parse the post date from meta tags."""
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        resp = await client.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 Chrome/125 Safari/537.36"
                )
            },
        )
        resp.raise_for_status()
        return extract_instagram_meta_date(resp.text)


def _extract_latest_content_date(content: str) -> Optional[datetime]:
    """Extract the latest explicit review/comment date from page text.

    Review pages often expose per-review dates in visible content, while page
    metadata has no article publication date.
    """
    dates = _extract_explicit_content_dates(content)

    if not dates:
        now = datetime.now(timezone.utc)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if _TODAY_RE.search(content):
            return today
        if _YESTERDAY_RE.search(content):
            return today - timedelta(days=1)
        days_ago = [
            int(match.group("days"))
            for match in _DAYS_AGO_RE.finditer(content)
            if int(match.group("days")) > 0
        ]
        if days_ago:
            return today - timedelta(days=min(days_ago))
        if _LONG_AGO_RE.search(content):
            return today - timedelta(days=365)
        return None
    now = datetime.now(timezone.utc)
    non_future = [dt for dt in dates if dt <= now]
    return max(non_future or dates)


async def scrape_url_with_meta(
    api_key: str, url: str
) -> Tuple[Optional[str], Optional[datetime]]:
    """Deep-scrape a URL and return (markdown_content, published_at).

    Requests 'markdown' from Firecrawl so we also get page metadata
    (Open Graph, schema.org) and can extract the publication date.
    Returns (None, None) on failure.
    Retries up to 3 times on 429 / 5xx / network errors.
    """
    if not api_key:
        raise ValueError("FIRECRAWL_API_KEY is empty")

    async def _request() -> Tuple[Optional[str], Optional[datetime]]:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{FIRECRAWL_API_BASE}/v1/scrape",
                json={
                    "url": url,
                    "formats": ["markdown"],
                    "onlyMainContent": True,
                },
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()

            if not (data.get("success") and data.get("data")):
                return None, None

            page = data["data"]
            content = page.get("markdown") or ""

            # Try to find publication date in metadata
            pub_date: Optional[datetime] = None
            meta = page.get("metadata") or {}
            for key in _PUB_DATE_KEYS:
                raw_date = meta.get(key)
                if raw_date:
                    pub_date = _parse_firecrawl_date(str(raw_date))
                    if pub_date:
                        break
            if pub_date is None and content:
                pub_date = _extract_latest_content_date(content)

            return content or None, pub_date

    return await with_retry(_request, label=f"firecrawl: {url[:60]}")


async def scrape_url(api_key: str, url: str) -> Optional[str]:
    """Deep-scrape a URL to clean Markdown using Firecrawl.

    Returns the page content in Markdown format (date is discarded).
    Kept for backward compatibility.
    """
    content, _ = await scrape_url_with_meta(api_key, url)
    return content


async def deep_read_url(api_key: str, url: str, max_chars: int = 8000) -> Optional[str]:
    """Scrape a URL and return a truncated version suitable for LLM context."""
    content = await scrape_url(api_key, url)
    if content and len(content) > max_chars:
        content = content[:max_chars] + "\n\n[... truncated]"
    return content
