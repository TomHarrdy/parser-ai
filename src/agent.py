"""
Agentic pipeline for brand mention monitoring.

Uses LangGraph to orchestrate:
  1. Collect (Tavily + Apify) → raw mentions
  2. Deduplicate (check PostgreSQL) → new mentions
  3. Analyze (LLM: sentiment + summary, Firecrawl deep-read if needed)
  4. Save + notify (PostgreSQL + Telegram)
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from langgraph.graph import StateGraph, END

from src.models import Mention, SourceType, Sentiment, EventType
from src.db import (
    get_session,
    get_active_lessons,
    get_due_comment_watchlist,
    get_watermark,
    is_url_stale,
    save_stale_url,
    set_watermark,
    check_and_update_content_snapshot,
    is_url_ignored,
    mention_exists_by_url,
    mention_exists_by_text_hash,
    update_comment_watchlist_check,
    upsert_comment_watchlist,
    url_hash,
    text_hash,
)
from src.freshness import (
    decide_freshness,
    extract_comment_snapshot,
    extract_latest_comment_date,
    looks_commentable,
)
from src.settings import Settings, get_settings
from src.tools import (
    fetch_instagram_meta_date,
    search_brand_mentions,
    search_monitoring_phrases,
    search_searxng_brand_mentions,
    search_searxng_monitoring_phrases,
    search_instagram_profiles,
    scrape_url_with_meta,
)
from src.tools.firecrawl import extract_first_content_date
from src.tools.tavily import extract_url
from src.tools.apify import search_vk, get_yandex_maps_reviews
from src.tools.vk_direct import VKDirectClient, search_vk_direct

logger = logging.getLogger(__name__)

TRUSTED_SOURCE_DATE_NAMES = {"vk", "vk_comment", "instagram", "instagram_comment"}

# Sources where ALL new content is always published without LLM relevance check.
# These are official owned channels of the brand — every new comment/post there
# is relevant by definition. LLM still runs for sentiment + summary, but the
# is_relevant field returned by LLM is ignored for these sources.
ALWAYS_RELEVANT_SOURCES = {"vk_comment"}


# ── State ────────────────────────────────────────────────

@dataclass
class PipelineState:
    """State that flows through the agent pipeline."""
    settings: Settings = field(default_factory=get_settings)
    raw_mentions: List[Dict[str, Any]] = field(default_factory=list)
    new_mentions: List[Dict[str, Any]] = field(default_factory=list)
    analyzed_mentions: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@dataclass
class RawMention:
    """A single raw mention from a tool."""
    url: Optional[str]
    text: str
    source_type: SourceType
    source_name: str = ""
    published_at: Optional[datetime] = None  # parsed from source's published_date
    is_content_update: bool = False


def _parse_published_date(raw: Optional[str]) -> Optional[datetime]:
    """Parse Tavily's published_date string (YYYY-MM-DD or ISO) into a UTC datetime.

    Returns None if the string is absent or unparseable.
    """
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw[:19], fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _coerce_published_at(raw: Any) -> Optional[datetime]:
    """Normalize a published_at value from pipeline dicts to UTC datetime."""
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, str):
        dt = _parse_published_date(raw)
        if dt is None:
            try:
                dt = datetime.fromisoformat(raw)
            except ValueError:
                return None
    else:
        return None

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _is_too_old(published_at: Optional[datetime], max_age_days: int) -> bool:
    """Return True if the article is older than max_age_days.

    Articles with no published_at are NOT filtered — we log them but let them
    through so we don't silently drop mentions from sources that don't expose dates.
    """
    if published_at is None:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    return published_at < cutoff


def _has_trusted_source_date(source_name: str, published_at: Optional[datetime]) -> bool:
    return bool(published_at and source_name in TRUSTED_SOURCE_DATE_NAMES)


# ── Node: Collect ────────────────────────────────────────

def _watermark_cutoff(
    watermark: Optional[datetime],
    overlap_minutes: int,
    fallback_age_days: int,
) -> datetime:
    """Calculate the cutoff time for watermark filtering.

    - If a watermark exists: cutoff = watermark - overlap_minutes (buffer for indexing delays)
    - First run (no watermark): cutoff = now() - fallback_age_days
    """
    now = datetime.now(timezone.utc)
    if watermark is not None:
        return watermark - timedelta(minutes=overlap_minutes)
    return now - timedelta(days=fallback_age_days)


def _is_before_watermark(pub: Optional[datetime], cutoff: datetime) -> bool:
    """Return True if pub is older than (or equal to) the cutoff.

    Items without a date are NOT filtered — we rely on URL/text dedup to catch
    duplicates from sources that don't expose publication dates.
    """
    if pub is None:
        return False
    return pub <= cutoff


async def _archive_for_freshness(
    url: Optional[str],
    text: str,
    source_name: str,
    published_at: Optional[datetime],
    reason: str,
) -> None:
    """Archive stale/missing-date URLs and queue commentable pages for monitoring."""
    if not url:
        return
    try:
        async for session in get_session():
            await save_stale_url(
                session,
                url=url,
                reason=reason,
                source_published_at=published_at,
                can_recheck_comments=looks_commentable(url, text),
            )
            if looks_commentable(url, text):
                await upsert_comment_watchlist(
                    session,
                    url=url,
                    source_name=source_name or "web",
                    main_published_at=published_at,
                    last_comment_at=extract_latest_comment_date(text),
                    comments_snapshot=extract_comment_snapshot(text),
                    check_interval_minutes=60,
                )
        logger.info("Archived URL for freshness [%s]: %s", reason, url)
    except Exception as e:
        logger.warning("Could not archive freshness URL %s: %s", url, e)


async def _load_watermark(source_name: str) -> Optional[datetime]:
    """Load watermark from DB for a given source. Returns None on first run or DB error."""
    try:
        async for session in get_session():
            return await get_watermark(session, source_name)
    except Exception as e:
        logger.warning("Could not load watermark for %s: %s", source_name, e)
    return None


async def _collect_tavily_brand(settings) -> Tuple[List[RawMention], List[str]]:
    """Collect brand mentions from Tavily (runs in parallel with other sources)."""
    raw: List[RawMention] = []
    errors: List[str] = []
    max_age = settings.max_article_age_days
    source = "tavily"

    if not (settings.tavily_api_key and settings.company_name):
        return raw, errors

    watermark = await _load_watermark(source)
    cutoff = _watermark_cutoff(
        watermark,
        overlap_minutes=settings.watermark_overlap_minutes,
        fallback_age_days=max_age,
    )
    logger.info("Tavily brand watermark cutoff: %s (watermark=%s)", cutoff, watermark)

    try:
        results = await search_brand_mentions(
            api_key=settings.tavily_api_key,
            brand=settings.company_name,
            keywords=settings.keyword_list,
            location=settings.monitoring_location,
            days=settings.tavily_search_days,
        )
        skipped = 0
        for r in results:
            pub = _parse_published_date(r.get("published_date"))
            if _is_too_old(pub, max_age) or _is_before_watermark(pub, cutoff):
                skipped += 1
                continue
            raw.append(RawMention(
                url=r.get("url"),
                text=r.get("content") or r.get("raw_content", ""),
                source_type=SourceType.web,
                source_name=source,
                published_at=pub,
            ))
        logger.info(
            "Tavily (brand): %d results, %d skipped (before watermark cutoff)",
            len(results), skipped,
        )
    except Exception as e:
        msg = f"Tavily brand search error [{type(e).__name__}]: {e}"
        logger.error(msg)
        errors.append(msg)
    return raw, errors


async def _collect_tavily_phrases(settings) -> Tuple[List[RawMention], List[str]]:
    """Collect phrase-based mentions from Tavily (runs in parallel with other sources)."""
    raw: List[RawMention] = []
    errors: List[str] = []
    max_age = settings.max_article_age_days
    source = "tavily_phrase"

    if not (settings.tavily_api_key and settings.search_phrase_list):
        return raw, errors

    watermark = await _load_watermark(source)
    cutoff = _watermark_cutoff(
        watermark,
        overlap_minutes=settings.watermark_overlap_minutes,
        fallback_age_days=max_age,
    )
    logger.info("Tavily phrases watermark cutoff: %s (watermark=%s)", cutoff, watermark)

    try:
        phrase_results = await search_monitoring_phrases(
            api_key=settings.tavily_api_key,
            phrases=settings.search_phrase_list,
            location=settings.monitoring_location,
            brand=settings.company_name or None,
            days=settings.tavily_search_days,
        )
        skipped = 0
        for r in phrase_results:
            pub = _parse_published_date(r.get("published_date"))
            if _is_too_old(pub, max_age) or _is_before_watermark(pub, cutoff):
                skipped += 1
                continue
            raw.append(RawMention(
                url=r.get("url"),
                text=r.get("content") or r.get("raw_content", ""),
                source_type=SourceType.web,
                source_name=source,
                published_at=pub,
            ))
        logger.info(
            "Tavily (phrases): %d results, %d skipped (before watermark cutoff)",
            len(phrase_results), skipped,
        )
    except Exception as e:
        msg = f"Tavily phrases error [{type(e).__name__}]: {e}"
        logger.error(msg)
        errors.append(msg)
    return raw, errors


async def _collect_searxng_brand(settings) -> Tuple[List[RawMention], List[str]]:
    """Collect brand mentions from a self-hosted SearXNG instance."""
    raw: List[RawMention] = []
    errors: List[str] = []
    max_age = settings.max_article_age_days
    source = "searxng"

    if not (settings.enable_searxng and settings.searxng_base_url and settings.company_name):
        return raw, errors

    watermark = await _load_watermark(source)
    cutoff = _watermark_cutoff(
        watermark,
        overlap_minutes=settings.watermark_overlap_minutes,
        fallback_age_days=max_age,
    )
    logger.info("SearXNG brand watermark cutoff: %s (watermark=%s)", cutoff, watermark)

    try:
        results = await search_searxng_brand_mentions(
            base_url=settings.searxng_base_url,
            brand=settings.company_name,
            keywords=settings.keyword_list,
            location=settings.monitoring_location,
            language=(settings.language_list[0] if settings.language_list else "ru"),
        )
        skipped = 0
        for r in results:
            pub = _parse_published_date(r.get("published_date"))
            if _is_too_old(pub, max_age) or _is_before_watermark(pub, cutoff):
                skipped += 1
                continue
            raw.append(RawMention(
                url=r.get("url"),
                text=r.get("content") or r.get("raw_content", ""),
                source_type=SourceType.web,
                source_name=source,
                published_at=pub,
            ))
        logger.info(
            "SearXNG (brand): %d results, %d skipped (before watermark cutoff)",
            len(results), skipped,
        )
    except Exception as e:
        msg = f"SearXNG brand search error [{type(e).__name__}]: {e}"
        logger.error(msg)
        errors.append(msg)
    return raw, errors


async def _collect_searxng_phrases(settings) -> Tuple[List[RawMention], List[str]]:
    """Collect phrase-based mentions from a self-hosted SearXNG instance."""
    raw: List[RawMention] = []
    errors: List[str] = []
    max_age = settings.max_article_age_days
    source = "searxng_phrase"

    if not (settings.enable_searxng and settings.searxng_base_url and settings.search_phrase_list):
        return raw, errors

    watermark = await _load_watermark(source)
    cutoff = _watermark_cutoff(
        watermark,
        overlap_minutes=settings.watermark_overlap_minutes,
        fallback_age_days=max_age,
    )
    logger.info("SearXNG phrases watermark cutoff: %s (watermark=%s)", cutoff, watermark)

    try:
        results = await search_searxng_monitoring_phrases(
            base_url=settings.searxng_base_url,
            phrases=settings.search_phrase_list,
            location=settings.monitoring_location,
            brand=settings.company_name or None,
            language=(settings.language_list[0] if settings.language_list else "ru"),
        )
        skipped = 0
        for r in results:
            pub = _parse_published_date(r.get("published_date"))
            if _is_too_old(pub, max_age) or _is_before_watermark(pub, cutoff):
                skipped += 1
                continue
            raw.append(RawMention(
                url=r.get("url"),
                text=r.get("content") or r.get("raw_content", ""),
                source_type=SourceType.web,
                source_name=source,
                published_at=pub,
            ))
        logger.info(
            "SearXNG (phrases): %d results, %d skipped (before watermark cutoff)",
            len(results), skipped,
        )
    except Exception as e:
        msg = f"SearXNG phrases error [{type(e).__name__}]: {e}"
        logger.error(msg)
        errors.append(msg)
    return raw, errors


async def _collect_vk(settings) -> Tuple[List[RawMention], List[str]]:
    """Collect VK posts via Apify (runs in parallel with other sources)."""
    raw: List[RawMention] = []
    errors: List[str] = []
    source = "vk"

    if settings.vk_access_token:
        return raw, errors

    vk_targets = settings.vk_target_list
    if not (settings.apify_api_key and (settings.company_name or vk_targets)):
        return raw, errors

    watermark = await _load_watermark(source)
    cutoff = _watermark_cutoff(
        watermark,
        overlap_minutes=settings.watermark_overlap_minutes,
        fallback_age_days=settings.max_article_age_days,
    )

    try:
        vk_results = await search_vk(
            api_key=settings.apify_api_key,
            query=settings.company_name,
            max_posts=10,
            targets=vk_targets or None,
        )
        skipped = 0
        for r in vk_results:
            text = r.get("text", "") or r.get("postText", "")
            pub = _parse_published_date(
                r.get("published_date") or r.get("postedAt") or r.get("date")
            )
            if _is_too_old(pub, settings.max_article_age_days) or _is_before_watermark(pub, cutoff):
                skipped += 1
                continue
            raw.append(RawMention(
                url=r.get("url") or r.get("postUrl") or r.get("sourceUrl"),
                text=text,
                source_type=SourceType.vk,
                source_name=source,
                published_at=pub,
            ))
        logger.info("VK: %d results, %d skipped (before watermark cutoff)", len(vk_results), skipped)
    except Exception as e:
        msg = f"VK error: {e}"
        logger.error(msg)
        errors.append(msg)
    return raw, errors


async def _collect_vk_direct(settings) -> Tuple[List[RawMention], List[str]]:
    """Collect VK posts via direct VK API (no Apify).

    Использует VK_ACCESS_TOKEN из .env.
    Если токен не задан — тихо пропускает (можно параллельно с Apify).
    """
    raw: List[RawMention] = []
    errors: List[str] = []
    source = "vk"

    vk_targets = settings.vk_target_list
    if not (settings.vk_access_token and (settings.company_name or vk_targets)):
        return raw, errors

    watermark = await _load_watermark(source)
    cutoff = _watermark_cutoff(
        watermark,
        overlap_minutes=settings.watermark_overlap_minutes,
        fallback_age_days=settings.max_article_age_days,
    )

    try:
        client = VKDirectClient(settings.vk_access_token)
        vk_results = await search_vk_direct(
            access_token=settings.vk_access_token,
            query=settings.company_name,
            max_posts=10,
            targets=vk_targets or None,
            with_comments=True,
        )
        skipped = 0
        for r in vk_results:
            text = r.get("text", "") or r.get("postText", "")
            pub = _parse_published_date(
                r.get("published_date") or r.get("postedAt") or r.get("date")
            )
            if _is_too_old(pub, settings.max_article_age_days) or _is_before_watermark(pub, cutoff):
                skipped += 1
                continue
            raw.append(RawMention(
                url=r.get("url") or r.get("postUrl") or r.get("sourceUrl"),
                text=text,
                source_type=SourceType.vk,
                source_name=source,
                published_at=pub,
            ))
            # Добавляем комментарии как отдельные упоминания
            comments = r.get("vk_comments", [])
            post_text_snippet = (r.get("text") or r.get("postText") or "").strip()[:300]
            for c in comments:
                c_pub = _parse_published_date(c.get("published_date") or c.get("date"))
                if _is_too_old(c_pub, settings.max_article_age_days) or _is_before_watermark(c_pub, cutoff):
                    continue
                c_text = c.get("text", "")
                if c_text.strip():
                    # Обогащаем текст контекстом: LLM должен знать что это
                    # комментарий на официальной странице компании ВКонтакте.
                    # Без этого короткие комментарии ("Отлично!", "Спасибо!")
                    # не содержат названия компании и LLM их отсеивает.
                    context_prefix = (
                        f"[Комментарий на странице {settings.company_name} ВКонтакте]"
                    )
                    if post_text_snippet:
                        enriched_text = (
                            f"{context_prefix}\n"
                            f"Пост компании: {post_text_snippet}\n\n"
                            f"Комментарий пользователя: {c_text}"
                        )
                    else:
                        enriched_text = f"{context_prefix}\n{c_text}"
                    raw.append(RawMention(
                        url=c.get("url"),
                        text=enriched_text,
                        source_type=SourceType.vk,
                        source_name="vk_comment",
                        published_at=c_pub,
                    ))
        logger.info("VK direct: %d results, %d skipped, %d comments",
                     len(vk_results), skipped,
                     sum(len(r.get("vk_comments", [])) for r in vk_results))
    except Exception as e:
        msg = f"VK direct error: {e}"
        logger.error(msg)
        errors.append(msg)
    return raw, errors


async def _collect_yandex_maps(settings) -> Tuple[List[RawMention], List[str]]:
    """Collect Yandex Maps reviews via Apify (runs in parallel with other sources)."""
    raw: List[RawMention] = []
    errors: List[str] = []
    source = "yandex_maps"

    if not (settings.apify_api_key and settings.yandex_maps_org_id):
        return raw, errors

    watermark = await _load_watermark(source)
    cutoff = _watermark_cutoff(
        watermark,
        overlap_minutes=settings.watermark_overlap_minutes,
        fallback_age_days=settings.max_article_age_days,
    )

    try:
        maps_results = await get_yandex_maps_reviews(
            api_key=settings.apify_api_key,
            organization_id=settings.yandex_maps_org_id,
            max_reviews=20,
        )
        skipped = 0
        for r in maps_results:
            text = r.get("text", "") or r.get("reviewText", "")
            pub = _parse_published_date(r.get("reviewDate") or r.get("date") or r.get("published_date"))
            if _is_too_old(pub, settings.max_article_age_days) or _is_before_watermark(pub, cutoff):
                skipped += 1
                continue
            raw.append(RawMention(
                url=r.get("reviewUrl") or r.get("url") or r.get("businessUrl"),
                text=text,
                source_type=SourceType.yandex_maps,
                source_name=source,
                published_at=pub,
            ))
        logger.info("Yandex Maps: %d results, %d skipped (before watermark cutoff)", len(maps_results), skipped)
    except Exception as e:
        msg = f"Yandex Maps error: {e}"
        logger.error(msg)
        errors.append(msg)
    return raw, errors


async def _collect_instagram_direct(settings) -> Tuple[List[RawMention], List[str]]:
    """Collect configured Instagram profile posts/comments via Instaloader."""
    raw: List[RawMention] = []
    errors: List[str] = []
    source = "instagram"

    if not (settings.enable_instagram and settings.instagram_target_list):
        return raw, errors

    watermark = await _load_watermark(source)
    cutoff = _watermark_cutoff(
        watermark,
        overlap_minutes=settings.watermark_overlap_minutes,
        fallback_age_days=settings.max_article_age_days,
    )

    try:
        results = await search_instagram_profiles(
            targets=settings.instagram_target_list,
            max_posts=settings.instagram_max_posts,
            max_comments=settings.instagram_max_comments,
            session_username=settings.instagram_session_username,
            session_file=settings.instagram_session_file,
        )
        skipped = 0
        comments = 0
        for r in results:
            source_name = r.get("source") or source
            pub = _parse_published_date(r.get("published_date"))
            if _is_too_old(pub, settings.max_article_age_days) or _is_before_watermark(pub, cutoff):
                skipped += 1
                continue
            if source_name == "instagram_comment":
                comments += 1
            raw.append(RawMention(
                url=r.get("url"),
                text=r.get("text") or "",
                source_type=SourceType.web,
                source_name=source_name,
                published_at=pub,
            ))
        logger.info(
            "Instagram direct: %d results, %d skipped, %d comments",
            len(results), skipped, comments,
        )
    except Exception as e:
        msg = f"Instagram direct error: {e}"
        logger.error(msg)
        errors.append(msg)
    return raw, errors


async def _collect_comment_watchlist(settings) -> Tuple[List[RawMention], List[str]]:
    """Monitor old/commentable pages for fresh comment/review activity."""
    raw: List[RawMention] = []
    errors: List[str] = []
    source = "comment_watchlist"

    if not settings.firecrawl_api_key:
        return raw, errors

    try:
        async for session in get_session():
            items = await get_due_comment_watchlist(session, limit=20)
            for item in items:
                try:
                    content, _ = await scrape_url_with_meta(settings.firecrawl_api_key, item.url)
                    if not content:
                        await update_comment_watchlist_check(
                            session, item, None, None, changed=False, failed=True
                        )
                        continue

                    snapshot = extract_comment_snapshot(content)
                    latest_comment_at = extract_latest_comment_date(content)
                    new_hash = text_hash(snapshot) if snapshot else None
                    changed = bool(
                        new_hash
                        and item.comments_snapshot_hash
                        and new_hash != item.comments_snapshot_hash
                    )
                    first_snapshot = bool(new_hash and not item.comments_snapshot_hash)

                    await update_comment_watchlist_check(
                        session,
                        item,
                        snapshot,
                        latest_comment_at,
                        changed=changed,
                        failed=False,
                    )

                    if first_snapshot:
                        logger.debug("Comment watchlist initialized: %s", item.url)
                        continue
                    if not changed:
                        continue
                    if not decide_freshness(
                        latest_comment_at,
                        settings.max_article_age_days,
                        is_content_update=True,
                    ).should_alert:
                        logger.info(
                            "Comment update skipped (not fresh/date missing): %s date=%s",
                            item.url,
                            latest_comment_at,
                        )
                        continue

                    raw.append(RawMention(
                        url=item.url,
                        text=snapshot or content,
                        source_type=SourceType.web,
                        source_name=source,
                        published_at=latest_comment_at,
                        is_content_update=True,
                    ))
                except Exception as e:
                    await update_comment_watchlist_check(
                        session, item, None, None, changed=False, failed=True
                    )
                    logger.warning("Comment watchlist error for %s: %s", item.url, e)
    except Exception as e:
        msg = f"Comment watchlist error: {e}"
        logger.error(msg)
        errors.append(msg)
    return raw, errors


async def collect_mentions(state: PipelineState) -> PipelineState:
    """Step 1: Fetch mentions from all configured tools IN PARALLEL."""
    settings = state.settings

    # Run all sources concurrently — each returns (List[RawMention], List[str])
    results = await asyncio.gather(
        _collect_tavily_brand(settings),
        _collect_tavily_phrases(settings),
        _collect_searxng_brand(settings),
        _collect_searxng_phrases(settings),
        _collect_vk(settings),
        _collect_vk_direct(settings),
        _collect_instagram_direct(settings),
        _collect_yandex_maps(settings),
        _collect_comment_watchlist(settings),
    )

    raw: List[RawMention] = []
    for batch_raw, batch_errors in results:
        raw.extend(batch_raw)
        state.errors.extend(batch_errors)

    logger.info("Collected %d raw mentions from all sources", len(raw))

    state.raw_mentions = [
        {
            "url": m.url,
            "text": m.text,
            "source_type": m.source_type.value,
            "source_name": m.source_name,
            "published_at": m.published_at.isoformat() if m.published_at else None,
            "is_content_update": m.is_content_update,
        }
        for m in raw
    ]
    return state


# ── Node: Verify Freshness ───────────────────────────────

async def _verify_single_freshness(
    m: Dict[str, Any],
    settings: Settings,
    semaphore: asyncio.Semaphore,
) -> Optional[Dict[str, Any]]:
    """Deep-read a collected URL and verify its real publication/activity date."""
    async with semaphore:
        url = m.get("url")
        text = m.get("text", "")
        source_name = m.get("source_name", "")
        is_update = bool(m.get("is_content_update"))
        published_at = _coerce_published_at(m.get("published_at"))

        if published_at is None and url and "instagram.com/" in url:
            snippet_date = extract_first_content_date(text)
            if snippet_date:
                published_at = snippet_date

        # Mandatory source verification for search results. Direct source APIs
        # like VK already provide trusted post/comment dates; web scraping those
        # URLs can return login/anti-bot pages with misleading dates.
        if url and not _has_trusted_source_date(source_name, published_at):
            try:
                deep_content, fc_pub_date = await scrape_url_with_meta(
                    settings.firecrawl_api_key if settings.enable_firecrawl_fallback else "",
                    url,
                    jina_reader_base_url=settings.jina_reader_base_url,
                )
                if deep_content and len(deep_content) > len(text):
                    text = deep_content
                    m["text"] = text
                    logger.info("Freshness verify deep-read: %s", url)
                if fc_pub_date:
                    published_at = _coerce_published_at(fc_pub_date)
            except Exception as e:
                logger.warning("Freshness verify page extraction error for %s: %s", url, e)

        if published_at is None and url and "instagram.com/" in url:
            try:
                published_at = await fetch_instagram_meta_date(url)
            except Exception as e:
                logger.warning("Freshness verify Instagram meta error for %s: %s", url, e)

        if url and len(text) < 200 and settings.tavily_api_key:
            try:
                extracted = await extract_url(settings.tavily_api_key, url)
                if extracted and len(extracted) > len(text):
                    text = extracted
                    m["text"] = text
                    if published_at is None:
                        published_at = extract_latest_comment_date(text)
            except Exception:
                pass

        if published_at:
            m["published_at"] = published_at.isoformat()

        freshness = decide_freshness(
            published_at,
            settings.max_article_age_days,
            is_content_update=is_update,
        )
        if not freshness.should_alert:
            await _archive_for_freshness(
                url,
                text,
                source_name,
                published_at,
                freshness.status,
            )
            logger.info(
                "Freshness verify skipped [%s date=%s]: %s",
                freshness.status,
                published_at,
                url or "no-url",
            )
            return None

        m["freshness_verified"] = True
        m["freshness_status"] = freshness.status
        m["activity_type"] = freshness.activity_type
        m["activity_published_at"] = (
            freshness.activity_published_at.isoformat()
            if freshness.activity_published_at
            else None
        )
        return m


async def verify_freshness(state: PipelineState) -> PipelineState:
    """Step 1.5: verify source dates before dedup and LLM analysis."""
    semaphore = asyncio.Semaphore(5)
    tasks = [
        _verify_single_freshness(m, state.settings, semaphore)
        for m in state.raw_mentions
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    verified: List[Dict[str, Any]] = []
    for raw in results:
        if isinstance(raw, Exception):
            msg = f"Freshness verify unexpected error: {raw}"
            logger.error(msg)
            state.errors.append(msg)
            continue
        if raw is not None:
            verified.append(raw)

    logger.info(
        "Freshness verify: %d raw → %d verified (%d skipped)",
        len(state.raw_mentions),
        len(verified),
        len(state.raw_mentions) - len(verified),
    )
    state.raw_mentions = verified
    return state


# ── Node: Deduplicate ────────────────────────────────────

async def deduplicate(state: PipelineState) -> PipelineState:
    """Step 2: Filter out already-seen mentions.

    Two dedup strategies depending on source type:

    STATIC sources (tavily, news):
      - Deduplicate by URL hash (article doesn't change after publication)
      - If URL already in DB → skip

    DYNAMIC sources (vk, yandex_maps, forums):
      - Use content-hash change detection via content_snapshots table
      - If URL never seen → process (new mention)
      - If URL seen AND content unchanged → skip (no new comments)
      - If URL seen AND content CHANGED → process (new comments/reviews!)
      - Mentions tagged with is_content_update=True for downstream context

    Both strategies also deduplicate within the current batch (same run).
    """
    settings = state.settings
    dynamic_sources = set(settings.dynamic_source_list)
    dynamic_sources.add("comment_watchlist")

    new: List[Dict[str, Any]] = []
    seen_urls_this_run: set = set()
    seen_text_hashes_this_run: set = set()

    async for session in get_session():
        for m in state.raw_mentions:
            url = m.get("url")
            text = m.get("text", "")
            source_name = m.get("source_name", "")

            if not text.strip():
                continue

            # ── Human blocklist: skip URLs ignored by operator ───
            if url and await is_url_ignored(session, url):
                logger.debug("Skipped (blocklisted by operator): %s", url)
                continue

            # ── Stale publication archive: skip old URLs unless this is a fresh
            # comment/update event from the watchlist.
            if url and not m.get("is_content_update") and await is_url_stale(session, url):
                logger.debug("Skipped (archived as stale): %s", url)
                continue

            # ── Within-run dedup (both strategies) ──────────────
            dedup_key = url or text_hash(text)
            if dedup_key in seen_urls_this_run:
                continue

            if m.get("is_content_update"):
                seen_urls_this_run.add(dedup_key)
                new.append(m)
                continue

            # ── Dynamic sources: content hash change detection ───
            if source_name in dynamic_sources and url:
                change = await check_and_update_content_snapshot(
                    session, url, source_name, text
                )
                if not change.should_process:
                    logger.debug(
                        "Content unchanged [%s]: %s", source_name, url
                    )
                    continue
                # Mark whether this is a new page or an update (comment appeared)
                m["is_content_update"] = change.has_changed
                if change.has_changed:
                    logger.info(
                        "Content UPDATED [%s] (new comments/reviews): %s",
                        source_name, url,
                    )
                seen_urls_this_run.add(dedup_key)
                new.append(m)
                continue

            # ── Static sources: URL / text hash dedup ────────────
            if url:
                exists = await mention_exists_by_url(session, url)
                if exists:
                    continue
                seen_urls_this_run.add(url)
                new.append(m)
            else:
                th = text_hash(text)
                if th in seen_text_hashes_this_run:
                    continue
                exists = await mention_exists_by_text_hash(session, text)
                if not exists:
                    seen_text_hashes_this_run.add(th)
                    new.append(m)

    skipped = len(state.raw_mentions) - len(new)
    updates = sum(1 for m in new if m.get("is_content_update"))
    logger.info(
        "Dedup: %d raw → %d new (%d fresh, %d content updates, %d skipped)",
        len(state.raw_mentions), len(new),
        len(new) - updates, updates, skipped,
    )
    state.new_mentions = new
    return state


# ── Node: Analyze ────────────────────────────────────────

def build_system_prompt(settings, lessons: list) -> str:
    """Build the LLM system prompt dynamically.

    Includes:
    - Company profile (name, description, location, keywords)
    - Explicit rules for what IS and IS NOT relevant
    - Lessons learned from past human feedback (accumulated false positives)
    """
    keywords_str = ", ".join(f'"{k}"' for k in settings.keyword_list[:20])
    phrases_str = (
        "\n".join(f"  - {p}" for p in settings.search_phrase_list[:15])
        if settings.search_phrase_list else "  (not configured)"
    )

    # Inject lessons as explicit anti-pattern rules
    lessons_block = ""
    if lessons:
        rules = "\n".join(
            f"  {i+1}. [{l.error_category}] {l.lesson_text}"
            for i, l in enumerate(lessons)
        )
        lessons_block = f"""
## Усвоенные уроки (правила на основе прошлых ошибок)
Ниже перечислены конкретные правила, выведенные из случаев когда агент ошибался.
Следуй им СТРОГО — они важнее общих правил выше:

{rules}
"""

    return f"""You are a brand monitoring analyst. Your job is to decide if a piece of online content is relevant to the company described below, and if so — analyze its sentiment.

## Профиль компании
- **Название**: {settings.company_name}
- **Описание**: {settings.company_description or "Не указано"}
- **Город/регион**: {settings.monitoring_location}
- **Ключевые слова для мониторинга**: {keywords_str}

## Отслеживаемые поисковые фразы
{phrases_str}

## Правила релевантности
Упоминание считается РЕЛЕВАНТНЫМ только если оно явно касается ИМЕННО ЭТОЙ компании:
- Прямо называет компанию "{settings.company_name}" в контексте её деятельности
- Упоминает конкретные локации, услуги, продукты или сотрудников этой компании
- Содержит отзыв или обсуждение опыта взаимодействия с этой компанией

Упоминание НЕ релевантно если:
- Название компании встречается случайно в другом контексте
- Речь идёт о компании с похожим названием из другого города
- Это совершенно другая компания или физическое лицо
- Упоминание касается конкурентов (даже прямых)
- Ключевое слово использовано в несвязанном контексте (метафора, другая отрасль){lessons_block}

## Формат ответа
Return ONLY valid JSON. No markdown, no explanation outside JSON."""


ANALYZE_USER_PROMPT = """Analyze the following content and return a JSON with these fields:

- "is_relevant": true if this content is about our company, false otherwise
- "event_type": one of "article" | "review" | "comment" | "post" | "forum" | "mention"
  - "article"  — news article, blog post, press release, media publication
  - "review"   — user review on maps, aggregator site, review platform
  - "comment"  — comment or reply under an article, post, or video
  - "post"     — social media post (VK, Telegram channel, etc.)
  - "forum"    — forum thread, discussion board, Q&A
  - "mention"  — any other mention type
- "sentiment": "positive" | "negative" | "neutral" (only meaningful if is_relevant=true)
- "reason": main topic in 3-7 words in Russian (e.g. "долгое ожидание", "приветливый персонал"). null if neutral or not relevant.
- "summary": 1-2 short natural Russian sentences in free form. Briefly describe the concrete event/fact from THIS content only: what happened, who/what is involved, and where/when if stated. Do not use a rigid template. Do not add generic brand descriptions, unrelated page sections, recommendations, "More posts", neighboring reviews, SEO text, or details not directly tied to the target content. Empty string if not relevant.

Date freshness policy:
- Current UTC date: {current_date}
- Freshness cutoff date: {cutoff_date}
- Source publication date: {source_published_at}
- If source_published_at is a known date AND it is older than the cutoff date, return is_relevant=false with reason "старая дата публикации".
- If the text itself explicitly mentions an old year or date (for example 2018, 2019, 2020, or a date clearly before the cutoff), return is_relevant=false with reason "старая дата публикации".
- IMPORTANT: Do NOT require the date to appear inside the text body. If source_published_at is provided (not "unknown"), treat it as the confirmed publication date. A review or comment that has a known source_published_at does NOT need a date in the text to be considered fresh.
- Only if source_published_at is "unknown" AND the text gives no date clues should you treat the freshness as uncertain — in that case still evaluate relevance normally; do not auto-reject just because the date is unknown.

Content to analyze:
---
{text}
---
"""


async def _load_lessons() -> list:
    """Load active feedback lessons from DB for system prompt injection."""
    try:
        async for session in get_session():
            return await get_active_lessons(session, limit=30)
    except Exception as e:
        logger.warning("Could not load feedback lessons: %s", e)
    return []


async def analyze_with_llm(
    settings: Settings,
    text: str,
    published_at: Optional[datetime] = None,
    force_relevant: bool = False,
) -> Dict[str, Any]:
    """Call LLM to analyze a mention text.

    Args:
        force_relevant: If True the mention comes from an official owned source
            (e.g. comments on the brand's own VK page). The LLM still runs for
            sentiment + summary, but is_relevant is forced to True regardless of
            what the model returns. Use for ALWAYS_RELEVANT_SOURCES.

    Uses structured system prompt (company profile + learned lessons from human
    feedback) and a user prompt (text to analyze). This separation allows the
    system prompt to be cached by the LLM provider (prompt caching).
    """
    if not settings.llm_api_key:
        raise ValueError(
            "LLM_API_KEY is not set. Please configure it in your .env file."
        )

    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_endpoint,
    )

    # Load active lessons from DB and inject into system prompt
    lessons = await _load_lessons()
    if lessons:
        logger.debug("Injecting %d feedback lesson(s) into system prompt", len(lessons))

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=settings.max_article_age_days)
    system_prompt = build_system_prompt(settings, lessons)

    # For official sources we tell the LLM up front that relevance is guaranteed
    # so it focuses on sentiment / summary quality rather than trying to filter.
    official_hint = (
        "\n\nIMPORTANT: This content comes from the brand's OFFICIAL channel "
        "(e.g. a comment on the company's own VK page). It is ALWAYS relevant. "
        "Set is_relevant=true unconditionally. Focus on sentiment and summary."
        if force_relevant
        else ""
    )

    user_prompt = ANALYZE_USER_PROMPT.format(
        text=text[:4000],
        current_date=now.strftime("%Y-%m-%d"),
        cutoff_date=cutoff.strftime("%Y-%m-%d"),
        source_published_at=(
            published_at.strftime("%Y-%m-%d") if published_at else "unknown"
        ),
    ) + official_hint

    resp = await client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=0.3,
        response_format={"type": "json_object"},
    )

    raw = resp.choices[0].message.content
    if not raw:
        return {"sentiment": "neutral", "reason": None, "summary": ""}

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON from the response
        import re
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            result = json.loads(match.group())
        else:
            return {
                "is_relevant": True if force_relevant else False,
                "sentiment": "neutral",
                "reason": None,
                "summary": raw[:200],
            }

    # Normalize
    # Official sources are always relevant regardless of what LLM returns.
    is_relevant = True if force_relevant else bool(result.get("is_relevant", False))

    sentiment = str(result.get("sentiment", "neutral")).lower()
    if sentiment not in ("positive", "negative", "neutral"):
        sentiment = "neutral"

    valid_event_types = {"article", "review", "comment", "post", "forum", "mention"}
    event_type = str(result.get("event_type", "mention")).lower()
    if event_type not in valid_event_types:
        event_type = "mention"

    return {
        "is_relevant": is_relevant,
        "event_type": event_type,
        "sentiment": sentiment,
        "reason": result.get("reason"),
        "summary": result.get("summary", "")[:500],
    }


async def _analyze_single(
    m: Dict[str, Any],
    settings,
    semaphore: asyncio.Semaphore,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Analyze one mention under a semaphore.

    Returns:
        (enriched_mention_or_None, error_message_or_None)
        - (dict, None)  — successfully analyzed and relevant
        - (None, None)  — successfully analyzed but irrelevant (filtered out)
        - (dict, msg)   — LLM error; mention kept with neutral defaults + error logged
    """
    async with semaphore:
        text = m.get("text", "")
        url = m.get("url")
        source_name = m.get("source_name", "")
        is_always_relevant = source_name in ALWAYS_RELEVANT_SOURCES

        published_at = _coerce_published_at(m.get("published_at"))
        if published_at is None and url and "instagram.com/" in url:
            published_at = extract_first_content_date(text)
            if published_at:
                m["published_at"] = published_at.isoformat()
                logger.debug("Instagram date extracted from snippet for %s: %s", url, published_at)

        # Official sources (ALWAYS_RELEVANT_SOURCES) already carry a trusted API
        # timestamp from the collector. Skip deep-read entirely:
        #   1. The date is already correct from VK/Instagram API.
        #   2. The text is already enriched with parent-post context.
        #   3. Scraping the post page could return the parent post's (old) date
        #      and accidentally override published_at, causing the freshness
        #      check below to wrongly drop the comment.
        needs_deep_read = (
            not is_always_relevant
            and not m.get("freshness_verified")
            and (len(text) < 200 or published_at is None)
        )
        if url and needs_deep_read:
            try:
                deep_content, fc_pub_date = await scrape_url_with_meta(
                    settings.firecrawl_api_key if settings.enable_firecrawl_fallback else "",
                    url,
                    jina_reader_base_url=settings.jina_reader_base_url,
                )
                if deep_content and len(deep_content) > len(text):
                    text = deep_content
                    logger.info("Page extraction deep-read: %s", url)
                # Use extracted date only if Tavily/source didn't provide one
                if fc_pub_date and published_at is None:
                    published_at = _coerce_published_at(fc_pub_date)
                    if published_at:
                        m["published_at"] = published_at.isoformat()
                        logger.debug("Page extraction date extracted for %s: %s", url, published_at)
            except Exception as e:
                logger.warning("Page extraction error for %s: %s", url, e)

        if published_at is None and url and "instagram.com/" in url:
            try:
                published_at = await fetch_instagram_meta_date(url)
                if published_at:
                    m["published_at"] = published_at.isoformat()
                    logger.debug("Instagram date extracted from meta for %s: %s", url, published_at)
            except Exception as e:
                logger.warning("Instagram meta date error for %s: %s", url, e)

        # Also try Tavily extract as fallback
        if url and len(text) < 200 and settings.tavily_api_key:
            try:
                extracted = await extract_url(settings.tavily_api_key, url)
                if extracted and len(extracted) > len(text):
                    text = extracted
            except Exception:
                pass

        # Official sources: skip age/freshness gates entirely.
        # The watermark in the collector already guarantees we only see new content.
        # Applying _is_too_old or decide_freshness here would wrongly drop comments
        # whose API-timestamp happens to be a few days old (e.g. posted during a gap
        # when the bot was down) but that we haven't processed yet.
        if is_always_relevant:
            logger.debug(
                "Official source (%s) — freshness gate skipped: %s",
                source_name,
                url or "no-url",
            )
            freshness = decide_freshness(
                published_at,
                settings.max_article_age_days,
                is_content_update=bool(m.get("is_content_update")),
            )
        else:
            if (
                not m.get("is_content_update")
                and _is_too_old(published_at, settings.max_article_age_days)
            ):
                await _archive_for_freshness(
                    url,
                    text,
                    source_name,
                    published_at,
                    "old_publication",
                )
                logger.info("Skipped (too old: %s): %s", published_at, url or "no-url")
                return None, None

            freshness = decide_freshness(
                published_at,
                settings.max_article_age_days,
                is_content_update=bool(m.get("is_content_update")),
            )
            if not freshness.should_alert:
                await _archive_for_freshness(
                    url,
                    text,
                    source_name,
                    published_at,
                    freshness.status,
                )
                logger.info(
                    "Skipped (freshness=%s, date=%s): %s",
                    freshness.status,
                    published_at,
                    url or "no-url",
                )
                return None, None

        # LLM analysis
        try:
            analysis = await analyze_with_llm(
                settings,
                text,
                published_at=published_at,
                force_relevant=is_always_relevant,
            )

            # Skip irrelevant mentions — they matched keywords but are not about the brand.
            # Official sources (ALWAYS_RELEVANT_SOURCES) are never skipped here because
            # force_relevant=True already sets is_relevant=True inside analyze_with_llm.
            if is_always_relevant:
                logger.info(
                    "Official source (%s) — relevance check bypassed: %s",
                    source_name,
                    url or "no-url",
                )
            elif not analysis.get("is_relevant", True):
                logger.info("Skipped (not relevant): %s", url or "no-url")
                return None, None

            event_type = analysis.get("event_type", "mention")
            # Trusted sources (VK, Instagram) provide their own timestamps —
            # do NOT skip them just because we couldn't resolve a date via
            # page-extraction. Only apply the hard date-gate for untrusted
            # web sources where event_type is review/comment.
            is_trusted_source = source_name in TRUSTED_SOURCE_DATE_NAMES
            if published_at is None and event_type in {"review", "comment"} and not is_trusted_source:
                logger.info(
                    "Skipped (missing source date for %s): %s",
                    event_type,
                    url or "no-url",
                )
                return None, None

            m["sentiment"] = analysis["sentiment"]
            m["event_type"] = event_type
            m["reason"] = analysis.get("reason")
            m["summary"] = analysis["summary"]
            m["activity_published_at"] = (
                freshness.activity_published_at.isoformat()
                if freshness.activity_published_at
                else None
            )
            m["activity_type"] = freshness.activity_type
            m["freshness_status"] = freshness.status
            logger.info(
                "Analyzed: %s → %s [%s]",
                url or "no-url", analysis["sentiment"], analysis.get("event_type", "mention"),
            )
            return m, None
        except Exception as e:
            err_msg = f"LLM analysis error for {url}: {e}"
            logger.error(err_msg)
            # Default values — keep the mention but mark as neutral
            m["sentiment"] = "neutral"
            m["reason"] = None
            m["summary"] = ""
            return m, err_msg


async def analyze_mentions(state: PipelineState) -> PipelineState:
    """Step 3: Analyze new mentions in PARALLEL (max 5 concurrent LLM calls)."""
    settings = state.settings

    # Limit concurrent LLM calls to avoid rate-limit errors
    semaphore = asyncio.Semaphore(5)

    tasks = [
        _analyze_single(m, settings, semaphore)
        for m in state.new_mentions
    ]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    analyzed: List[Dict[str, Any]] = []
    for raw in raw_results:
        if isinstance(raw, Exception):
            # Unexpected gather-level error (should not happen normally)
            msg = f"Unexpected gather error: {raw}"
            logger.error(msg)
            state.errors.append(msg)
            continue
        mention, error = raw
        if error:
            state.errors.append(error)
        if mention is not None:
            analyzed.append(mention)

    logger.info(
        "Analysis: %d processed, %d relevant, %d irrelevant/skipped",
        len(state.new_mentions),
        len(analyzed),
        len(state.new_mentions) - len(analyzed),
    )

    state.analyzed_mentions = analyzed
    return state


# ── Node: Save & Notify ──────────────────────────────────

async def save_and_notify(state: PipelineState) -> PipelineState:
    """Step 4: Save analyzed mentions to PostgreSQL and send Telegram alerts.

    Mentions are saved first (all in one session), then alerts are sent in bulk
    over a single Telegram HTTP session to avoid creating one Bot per mention.
    """
    from src.telegram import send_alerts_bulk

    settings = state.settings
    saved_mentions: List[Mention] = []

    async for session in get_session():
        for m in state.analyzed_mentions:
            url = m.get("url")
            text = m.get("text", "")
            source_type_str = m.get("source_type", "other")

            try:
                source_type = SourceType(source_type_str)
            except ValueError:
                source_type = SourceType.other

            try:
                sentiment = Sentiment(m.get("sentiment", "neutral"))
            except ValueError:
                sentiment = Sentiment.neutral

            # For URL-less mentions, use text hash as url_hash to satisfy the
            # NOT NULL UNIQUE constraint. Dedup already ensures we won't insert
            # a duplicate text, so collisions here are not possible in practice.
            source_published_at = _coerce_published_at(m.get("published_at"))
            activity_published_at = _coerce_published_at(m.get("activity_published_at"))
            freshness_status = m.get("freshness_status")
            activity_type = m.get("activity_type")
            if m.get("is_content_update") and url:
                event_key = (
                    activity_published_at.isoformat()
                    if activity_published_at
                    else text_hash(text)
                )
                mention_url_hash = url_hash(f"{url}#activity:{event_key}")
            else:
                mention_url_hash = url_hash(url) if url else text_hash(text)

            # Parse event_type from LLM result
            try:
                event_type = EventType(m.get("event_type", "mention"))
            except ValueError:
                event_type = EventType.mention

            mention = Mention(
                url=url,
                url_hash=mention_url_hash,
                source_type=source_type,
                raw_text=text,
                raw_text_hash=text_hash(text),
                ai_summary=m.get("summary", ""),
                ai_reason=m.get("reason"),
                event_type=event_type,
                sentiment=sentiment,
                is_alert_sent=False,
                source_published_at=source_published_at,
                activity_published_at=activity_published_at or source_published_at,
                activity_type=activity_type,
                freshness_status=freshness_status,
            )

            try:
                session.add(mention)
                await session.commit()
                saved_mentions.append(mention)
            except Exception as db_err:
                await session.rollback()
                logger.warning(
                    "Skipped saving mention (likely duplicate): %s — %s",
                    url or "no-url",
                    db_err,
                )

    logger.info("Saved %d mentions", len(saved_mentions))

    # Collect IDs of mentions that are content updates (new comments on old pages)
    # so Telegram can format them differently
    content_update_mention_ids = {
        saved.id
        for saved, m in zip(saved_mentions, state.analyzed_mentions)
        if m.get("is_content_update")
    }

    # Send all alerts in a single Telegram session (one HTTP connection)
    if saved_mentions:
        alert_results = await send_alerts_bulk(
            settings, saved_mentions, content_update_ids=content_update_mention_ids
        )

        # Mark sent alerts in DB
        async for session in get_session():
            for mention, sent in zip(saved_mentions, alert_results):
                if sent:
                    mention.is_alert_sent = True
                    session.add(mention)
            await session.commit()

    # ── Update high-water marks ──────────────────────────
    # Record run_at = now() per source so the NEXT run filters out already-seen content.
    # We do this after a successful save — if the pipeline crashed mid-run, no watermark
    # is written, so the next run will retry the same window (safe due to dedup).
    now = datetime.now(timezone.utc)
    sources_in_run = {
        m.get("source_name") for m in state.analyzed_mentions if m.get("source_name")
    }
    if sources_in_run:
        try:
            async for session in get_session():
                for source_name in sources_in_run:
                    await set_watermark(session, source_name, now)
            logger.info("Watermarks updated for sources: %s", sorted(sources_in_run))
        except Exception as e:
            logger.warning("Failed to update watermarks: %s", e)

    return state


# ── Build the Graph ──────────────────────────────────────

def build_pipeline() -> StateGraph:
    """Build the LangGraph state machine for the monitoring pipeline."""
    graph = StateGraph(PipelineState)

    graph.add_node("collect", collect_mentions)
    graph.add_node("verify_freshness", verify_freshness)
    graph.add_node("deduplicate", deduplicate)
    graph.add_node("analyze", analyze_mentions)
    graph.add_node("save_and_notify", save_and_notify)

    graph.set_entry_point("collect")
    graph.add_edge("collect", "verify_freshness")
    graph.add_edge("verify_freshness", "deduplicate")
    graph.add_edge("deduplicate", "analyze")
    graph.add_edge("analyze", "save_and_notify")
    graph.add_edge("save_and_notify", END)

    return graph


async def run_pipeline(settings: Optional[Settings] = None) -> PipelineState:
    """Run the full monitoring pipeline once."""
    if settings is None:
        settings = get_settings()

    graph = build_pipeline()
    app = graph.compile()

    initial_state = PipelineState(settings=settings)
    result = await app.ainvoke(initial_state)

    # app.ainvoke returns the full state dict, not PipelineState directly
    # Handle both cases
    if isinstance(result, dict):
        final = PipelineState(**result)
    else:
        final = result

    logger.info(
        "Pipeline complete: %d collected, %d new, %d analyzed. Errors: %d",
        len(final.raw_mentions),
        len(final.new_mentions),
        len(final.analyzed_mentions),
        len(final.errors),
    )

    return final
