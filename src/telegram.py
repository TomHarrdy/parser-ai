"""Telegram alert sender using aiogram."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator, List

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from src.models import Mention
from src.settings import Settings

logger = logging.getLogger(__name__)

# Sentiment emoji map
SENTIMENT_EMOJI = {
    "positive": "🟢",
    "negative": "🔴",
    "neutral": "⚪️",
}


def _format_alert(mention: Mention) -> str:
    emoji = SENTIMENT_EMOJI.get(
        mention.sentiment.value if mention.sentiment else "neutral", "⚪️"
    )

    lines = [
        f"{emoji} **Новое упоминание**",
        "",
    ]

    if mention.ai_summary:
        lines.append(f"📝 {mention.ai_summary}")
        lines.append("")

    if mention.url:
        lines.append(f"🔗 [Источник]({mention.url})")
        lines.append("")

    lines.append(f"📡 Источник: `{mention.source_type.value}`")

    if mention.source_published_at:
        pub_ts = mention.source_published_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
        lines.append(f"📅 Опубликовано: {pub_ts}")

    ts = mention.created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"🕐 Добавлено: {ts}")

    return "\n".join(lines)


@asynccontextmanager
async def _bot_session(settings: Settings) -> AsyncIterator[Bot]:
    """Context manager that creates a single Bot instance and closes it on exit.

    Use this to share one HTTP session across multiple send_message calls
    instead of opening a new connection per mention.
    """
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )
    try:
        yield bot
    finally:
        await bot.session.close()


async def send_alert(settings: Settings, mention: Mention) -> None:
    """Send a Telegram alert for a single mention (opens its own session).

    For bulk sending prefer send_alerts_bulk() to reuse one HTTP connection.
    """
    if not settings.telegram_bot_token or not settings.telegram_target_chat_id:
        logger.debug("Telegram not configured, skipping alert")
        return

    async with _bot_session(settings) as bot:
        text = _format_alert(mention)
        await bot.send_message(
            chat_id=settings.telegram_target_chat_id,
            text=text,
            disable_web_page_preview=False,
        )
        logger.info("Alert sent for %s", mention.id)


async def send_alerts_bulk(settings: Settings, mentions: List[Mention]) -> List[bool]:
    """Send Telegram alerts for multiple mentions over a single HTTP session.

    Returns a list of booleans indicating success for each mention.
    """
    if not settings.telegram_bot_token or not settings.telegram_target_chat_id:
        logger.debug("Telegram not configured, skipping bulk alerts")
        return [False] * len(mentions)

    results: List[bool] = []

    async with _bot_session(settings) as bot:
        for mention in mentions:
            try:
                text = _format_alert(mention)
                await bot.send_message(
                    chat_id=settings.telegram_target_chat_id,
                    text=text,
                    disable_web_page_preview=False,
                )
                logger.info("Alert sent for %s", mention.id)
                results.append(True)
            except Exception as e:
                logger.error("Failed to send alert for %s: %s", mention.id, e)
                results.append(False)

    return results
