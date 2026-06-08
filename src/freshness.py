"""Freshness and comment-watch helpers for parser alerts."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.tools.firecrawl import _extract_latest_content_date


COMMENT_MARKERS = (
    "комментар",
    "отзыв",
    "обсуждени",
    "reply",
    "comment",
    "review",
)


@dataclass
class FreshnessDecision:
    status: str
    activity_type: Optional[str] = None
    activity_published_at: Optional[datetime] = None

    @property
    def should_alert(self) -> bool:
        return self.status in {"confirmed_fresh", "fresh_comment_on_old_page"}


def is_fresh(dt: Optional[datetime], max_age_days: int) -> bool:
    if dt is None:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    return dt >= cutoff


def decide_freshness(
    published_at: Optional[datetime],
    max_age_days: int,
    is_content_update: bool = False,
) -> FreshnessDecision:
    """Return the alerting decision for a publication/comment activity."""
    if published_at is None:
        return FreshnessDecision(status="missing_date")
    if not is_fresh(published_at, max_age_days):
        return FreshnessDecision(
            status="old",
            activity_type="comment" if is_content_update else "publication",
            activity_published_at=published_at,
        )
    if is_content_update:
        return FreshnessDecision(
            status="fresh_comment_on_old_page",
            activity_type="comment",
            activity_published_at=published_at,
        )
    return FreshnessDecision(
        status="confirmed_fresh",
        activity_type="publication",
        activity_published_at=published_at,
    )


def looks_commentable(url: Optional[str], text: str) -> bool:
    """Heuristic: page can receive comments/reviews/discussion updates."""
    combined = f"{url or ''}\n{text or ''}".lower()
    return any(marker in combined for marker in COMMENT_MARKERS)


def extract_comment_snapshot(text: str, max_lines: int = 80) -> str:
    """Extract a stable-ish comment/review slice from page text.

    This intentionally avoids hashing the whole page. It keeps lines near
    comment/review markers and explicit dates, which is enough to detect new
    user activity without reacting to every layout/sidebar change.
    """
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    kept: list[str] = []
    date_like = re.compile(r"(20\d{2}|\d{1,2}[./-]\d{1,2}[./-]20\d{2}|сегодня|вчера)", re.I)
    for idx, line in enumerate(lines):
        low = line.lower()
        if any(marker in low for marker in COMMENT_MARKERS) or date_like.search(line):
            start = max(0, idx - 1)
            end = min(len(lines), idx + 3)
            kept.extend(lines[start:end])
        if len(kept) >= max_lines:
            break
    if not kept:
        kept = lines[-min(len(lines), 30):]
    # Preserve order while removing duplicates.
    seen: set[str] = set()
    unique = []
    for line in kept:
        if line not in seen:
            seen.add(line)
            unique.append(line)
    return "\n".join(unique[:max_lines])


def extract_latest_comment_date(text: str) -> Optional[datetime]:
    snapshot = extract_comment_snapshot(text)
    return _extract_latest_content_date(snapshot or text)
