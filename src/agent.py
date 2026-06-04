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
from typing import Any, Dict, List, Optional
from uuid import UUID

from langgraph.graph import StateGraph, END

from src.models import Mention, SourceType, Sentiment
from src.db import (
    get_session,
    mention_exists_by_url,
    mention_exists_by_text_hash,
    url_hash,
    text_hash,
)
from src.settings import Settings, get_settings
from src.tools import search_brand_mentions, search_monitoring_phrases, deep_read_url
from src.tools.tavily import extract_url
from src.tools.apify import search_vk, get_yandex_maps_reviews

logger = logging.getLogger(__name__)


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


def _is_too_old(published_at: Optional[datetime], max_age_days: int) -> bool:
    """Return True if the article is older than max_age_days.

    Articles with no published_at are NOT filtered — we log them but let them
    through so we don't silently drop mentions from sources that don't expose dates.
    """
    if published_at is None:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    return published_at < cutoff


# ── Node: Collect ────────────────────────────────────────

async def collect_mentions(state: PipelineState) -> PipelineState:
    """Step 1: Fetch mentions from all configured tools."""
    settings = state.settings
    raw: List[RawMention] = []

    max_age = settings.max_article_age_days

    # Tavily Search — brand mentions
    if settings.tavily_api_key and settings.company_name:
        try:
            results = await search_brand_mentions(
                api_key=settings.tavily_api_key,
                brand=settings.company_name,
                keywords=settings.keyword_list,
                location=settings.monitoring_location,
            )
            skipped_old = 0
            for r in results:
                pub = _parse_published_date(r.get("published_date"))
                if _is_too_old(pub, max_age):
                    skipped_old += 1
                    continue
                raw.append(RawMention(
                    url=r.get("url"),
                    text=r.get("content") or r.get("raw_content", ""),
                    source_type=SourceType.web,
                    source_name="tavily",
                    published_at=pub,
                ))
            logger.info(
                "Tavily (brand): %d results, %d skipped (older than %d days)",
                len(results), skipped_old, max_age,
            )
        except Exception as e:
            msg = f"Tavily error: {e}"
            logger.error(msg)
            state.errors.append(msg)

    # Tavily Search — monitoring phrases
    if settings.tavily_api_key and settings.search_phrase_list:
        try:
            phrase_results = await search_monitoring_phrases(
                api_key=settings.tavily_api_key,
                phrases=settings.search_phrase_list,
                location=settings.monitoring_location,
            )
            skipped_old = 0
            for r in phrase_results:
                pub = _parse_published_date(r.get("published_date"))
                if _is_too_old(pub, max_age):
                    skipped_old += 1
                    continue
                raw.append(RawMention(
                    url=r.get("url"),
                    text=r.get("content") or r.get("raw_content", ""),
                    source_type=SourceType.web,
                    source_name="tavily_phrase",
                    published_at=pub,
                ))
            logger.info(
                "Tavily (phrases): %d results, %d skipped (older than %d days)",
                len(phrase_results), skipped_old, max_age,
            )
        except Exception as e:
            msg = f"Tavily phrases error: {e}"
            logger.error(msg)
            state.errors.append(msg)

    # Apify VK
    if settings.apify_api_key and settings.company_name:
        try:
            vk_results = await search_vk(
                api_key=settings.apify_api_key,
                query=settings.company_name,
                max_posts=10,
            )
            for r in vk_results:
                text = r.get("text", "") or r.get("postText", "")
                raw.append(RawMention(
                    url=r.get("url") or r.get("postUrl"),
                    text=text,
                    source_type=SourceType.vk,
                    source_name="vk",
                ))
            logger.info("VK: %d results", len(vk_results))
        except Exception as e:
            msg = f"VK error: {e}"
            logger.error(msg)
            state.errors.append(msg)

    # Apify Yandex Maps
    if settings.apify_api_key and settings.yandex_maps_org_id:
        try:
            maps_results = await get_yandex_maps_reviews(
                api_key=settings.apify_api_key,
                organization_id=settings.yandex_maps_org_id,
                max_reviews=20,
            )
            for r in maps_results:
                text = r.get("text", "") or r.get("reviewText", "")
                raw.append(RawMention(
                    url=r.get("reviewUrl") or r.get("url"),
                    text=text,
                    source_type=SourceType.yandex_maps,
                    source_name="yandex_maps",
                ))
            logger.info("Yandex Maps: %d results", len(maps_results))
        except Exception as e:
            msg = f"Yandex Maps error: {e}"
            logger.error(msg)
            state.errors.append(msg)

    state.raw_mentions = [
        {
            "url": m.url,
            "text": m.text,
            "source_type": m.source_type.value,
            "source_name": m.source_name,
            "published_at": m.published_at.isoformat() if m.published_at else None,
        }
        for m in raw
    ]
    return state


# ── Node: Deduplicate ────────────────────────────────────

async def deduplicate(state: PipelineState) -> PipelineState:
    """Step 2: Filter out already-seen mentions."""
    new: List[Dict[str, Any]] = []

    async for session in get_session():
        for m in state.raw_mentions:
            url = m.get("url")
            text = m.get("text", "")

            if not text.strip():
                continue

            if url:
                exists = await mention_exists_by_url(session, url)
            else:
                exists = await mention_exists_by_text_hash(session, text)

            if not exists:
                new.append(m)

    skipped = len(state.raw_mentions) - len(new)
    logger.info("Dedup: %d raw → %d new (skipped %d)", len(state.raw_mentions), len(new), skipped)
    state.new_mentions = new
    return state


# ── Node: Analyze ────────────────────────────────────────

ANALYZE_PROMPT = """You are a brand monitoring analyst.

Read the following text about the company "{brand}".

1. Determine the sentiment: positive, negative, or neutral.
2. If this is a complaint or praise, identify the main reason.
3. Write a 1-2 sentence summary in Russian.

Return ONLY valid JSON with these keys:
- "sentiment": "positive" | "negative" | "neutral"
- "reason": string (or null if not applicable)
- "summary": string (1-2 sentences in Russian)

Text to analyze:
---
{text}
---
"""


async def analyze_with_llm(settings: Settings, text: str) -> Dict[str, Any]:
    """Call LLM to analyze a mention text. Returns {sentiment, reason, summary}."""
    if not settings.llm_api_key:
        raise ValueError(
            "LLM_API_KEY is not set. Please configure it in your .env file."
        )

    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_endpoint,
    )

    prompt = ANALYZE_PROMPT.format(brand=settings.company_name, text=text[:6000])

    resp = await client.chat.completions.create(
        model=settings.llm_model,
        messages=[{"role": "user", "content": prompt}],
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
            return {"sentiment": "neutral", "reason": None, "summary": raw[:200]}

    # Normalize
    sentiment = str(result.get("sentiment", "neutral")).lower()
    if sentiment not in ("positive", "negative", "neutral"):
        sentiment = "neutral"

    return {
        "sentiment": sentiment,
        "reason": result.get("reason"),
        "summary": result.get("summary", "")[:500],
    }


async def analyze_mentions(state: PipelineState) -> PipelineState:
    """Step 3: For each new mention, optionally deep-read and then analyze with LLM."""
    settings = state.settings
    analyzed: List[Dict[str, Any]] = []

    for m in state.new_mentions:
        text = m.get("text", "")
        url = m.get("url")

        # If text is too short (snippet), try to deep-read the URL
        if url and len(text) < 200 and settings.firecrawl_api_key:
            try:
                deep_content = await deep_read_url(settings.firecrawl_api_key, url)
                if deep_content and len(deep_content) > len(text):
                    text = deep_content
                    logger.info("Firecrawl deep-read: %s", url)
            except Exception as e:
                logger.warning("Firecrawl error for %s: %s", url, e)

        # Also try Tavily extract as fallback
        if url and len(text) < 200 and settings.tavily_api_key:
            try:
                extracted = await extract_url(settings.tavily_api_key, url)
                if extracted and len(extracted) > len(text):
                    text = extracted
            except Exception:
                pass

        # LLM analysis
        try:
            analysis = await analyze_with_llm(settings, text)
            m["sentiment"] = analysis["sentiment"]
            m["reason"] = analysis.get("reason")
            m["summary"] = analysis["summary"]
            analyzed.append(m)
            logger.info("Analyzed: %s → %s", url or "no-url", analysis["sentiment"])
        except Exception as e:
            msg = f"LLM analysis error for {url}: {e}"
            logger.error(msg)
            state.errors.append(msg)
            # Default values
            m["sentiment"] = "neutral"
            m["reason"] = None
            m["summary"] = ""
            analyzed.append(m)

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
            # Parse published_at from ISO string stored in analyzed mention dict
            pub_str = m.get("published_at")
            source_published_at: Optional[datetime] = None
            if pub_str:
                try:
                    source_published_at = datetime.fromisoformat(pub_str)
                except (ValueError, TypeError):
                    pass

            mention = Mention(
                url=url,
                url_hash=url_hash(url) if url else text_hash(text),
                source_type=source_type,
                raw_text=text,
                raw_text_hash=text_hash(text),
                ai_summary=m.get("summary", ""),
                sentiment=sentiment,
                is_alert_sent=False,
                source_published_at=source_published_at,
            )

            session.add(mention)
            await session.commit()
            saved_mentions.append(mention)

    logger.info("Saved %d mentions", len(saved_mentions))

    # Send all alerts in a single Telegram session (one HTTP connection)
    if saved_mentions:
        alert_results = await send_alerts_bulk(settings, saved_mentions)

        # Mark sent alerts in DB
        async for session in get_session():
            for mention, sent in zip(saved_mentions, alert_results):
                if sent:
                    mention.is_alert_sent = True
                    session.add(mention)
            await session.commit()

    return state


# ── Build the Graph ──────────────────────────────────────

def build_pipeline() -> StateGraph:
    """Build the LangGraph state machine for the monitoring pipeline."""
    graph = StateGraph(PipelineState)

    graph.add_node("collect", collect_mentions)
    graph.add_node("deduplicate", deduplicate)
    graph.add_node("analyze", analyze_mentions)
    graph.add_node("save_and_notify", save_and_notify)

    graph.set_entry_point("collect")
    graph.add_edge("collect", "deduplicate")
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
