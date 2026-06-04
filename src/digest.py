"""Weekly digest generator.

Once a week, queries all mentions from the last 7 days and asks the LLM
to generate an analytical report: trends, top complaint topics, statistics.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import List

from sqlalchemy import select

from src.db import get_session
from src.models import Mention, Sentiment
from src.settings import Settings, get_settings

logger = logging.getLogger(__name__)

DIGEST_PROMPT = """You are a brand reputation analyst.

Below is a list of all online mentions of the company "{brand}" from the past week.
Each mention includes: source type, AI-generated summary, and sentiment.

Generate a weekly analytical report in Russian with the following sections:

1. **Общая статистика**: total mentions, unique sources, sentiment breakdown (% positive, negative, neutral).

2. **Главные темы недели**: group mentions into 3-5 key topics/themes. For each topic, state the predominant sentiment, list 2-3 examples.

3. **Негативные упоминания**: if there are negative mentions, summarize the main complaints and their frequency.

4. **Позитивные упоминания**: key praise points.

5. **Потенциальные риски**: any emerging risks or patterns to watch.

6. **Рекомендации**: 2-4 actionable recommendations based on this week's mentions.

Report format: Markdown, in Russian. Be concise but data-driven.

Mentions data:
---
{mentions_json}
---
"""


async def _fetch_week_mentions() -> List[dict]:
    """Fetch all mentions from the last 7 days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    async for session in get_session():
        result = await session.execute(
            select(Mention)
            .where(Mention.created_at >= cutoff)
            .order_by(Mention.created_at.desc())
        )
        mentions = result.scalars().all()

        return [
            {
                "source_type": m.source_type.value if m.source_type else "unknown",
                "sentiment": m.sentiment.value if m.sentiment else "neutral",
                "summary": m.ai_summary or "",
                "url": m.url or "",
                "date": m.created_at.isoformat() if m.created_at else "",
            }
            for m in mentions
        ]


async def generate_digest(settings: Settings = None) -> str:
    """Generate a weekly digest and return it as Markdown text."""
    if settings is None:
        settings = get_settings()

    mentions = await _fetch_week_mentions()

    if not mentions:
        return "📊 *Еженедельный дайджест*\n\nЗа последнюю неделю упоминаний не найдено."

    logger.info("Generating digest for %d mentions", len(mentions))

    # Generate via LLM
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_endpoint,
    )

    prompt = DIGEST_PROMPT.format(
        brand=settings.company_name,
        mentions_json=json.dumps(mentions, ensure_ascii=False, indent=2),
    )

    resp = await client.chat.completions.create(
        model=settings.llm_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5,
        max_tokens=3000,
    )

    content = resp.choices[0].message.content or ""

    # Add header
    week_start = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%d.%m.%Y")
    week_end = datetime.now(timezone.utc).strftime("%d.%m.%Y")

    header = (
        f"📊 *Еженедельный дайджест упоминаний*\n"
        f"Компания: *{settings.company_name}*\n"
        f"Период: {week_start} — {week_end}\n"
        f"Найдено упоминаний: {len(mentions)}\n\n"
        f"---\n\n"
    )

    return header + content


async def send_weekly_digest(settings: Settings = None) -> None:
    """Generate and send the weekly digest to Telegram."""
    if settings is None:
        settings = get_settings()

    if not settings.telegram_bot_token or not settings.telegram_target_chat_id:
        logger.warning("Telegram not configured, cannot send digest")
        return

    digest_text = await generate_digest(settings)

    from src.telegram import _bot_session

    # Reuse a single Bot HTTP session for all parts of the digest
    async with _bot_session(settings) as bot:
        # Split long messages if needed (Telegram hard limit is 4096 chars)
        max_len = 4000
        parts = (
            [digest_text]
            if len(digest_text) <= max_len
            else [digest_text[i : i + max_len] for i in range(0, len(digest_text), max_len)]
        )
        for part in parts:
            await bot.send_message(
                chat_id=settings.telegram_target_chat_id,
                text=part,
            )
        logger.info("Weekly digest sent (%d part(s))", len(parts))
