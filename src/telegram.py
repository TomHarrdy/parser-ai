"""Telegram alert sender using aiogram."""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator, List, Optional, Set
from urllib.parse import urlparse

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from src.models import Mention
from src.settings import Settings

logger = logging.getLogger(__name__)

# Sentiment labels and emoji
SENTIMENT_EMOJI = {
    "positive": "🟢",
    "negative": "🔴",
    "neutral": "⚪️",
}
SENTIMENT_LABEL = {
    "positive": "Позитив",
    "negative": "Негатив",
    "neutral": "Нейтрально",
}

# Event type labels and emoji
EVENT_TYPE_LABEL = {
    "article": ("📰", "Статья"),
    "review":  ("⭐", "Отзыв"),
    "comment": ("💬", "Комментарий"),
    "post":    ("📢", "Пост"),
    "forum":   ("🗣", "Форум"),
    "mention": ("📌", "Упоминание"),
    "unknown": ("❓", "Неизвестно"),
}

# Human-readable site names by domain
DOMAIN_LABELS: dict[str, str] = {
    # Социальные сети
    "vk.com":           "ВКонтакте",
    "instagram.com":    "Instagram",
    "t.me":             "Telegram",
    "youtube.com":      "YouTube",
    "youtu.be":         "YouTube",
    "facebook.com":     "Facebook",
    "ok.ru":            "Одноклассники",
    # Карты и отзовики
    "yandex.ru":        "Яндекс",
    "yandex.com":       "Яндекс",
    "maps.yandex.ru":   "Яндекс.Карты",
    "2gis.ru":          "2ГИС",
    "tripadvisor.ru":   "TripAdvisor",
    "tripadvisor.com":  "TripAdvisor",
    "otzovik.com":      "Отзовик",
    "irecommend.ru":    "iRecommend",
    "yell.ru":          "Yell",
    "zoon.ru":          "Zoon",
    # Новостные СМИ
    "vedomosti.ru":     "Ведомости",
    "rbc.ru":           "РБК",
    "kommersant.ru":    "Коммерсантъ",
    "ria.ru":           "РИА Новости",
    "tass.ru":          "ТАСС",
    "lenta.ru":         "Lenta.ru",
    "kp.ru":            "Комсомольская правда",
    "mk.ru":            "МК",
    "mos.ru":           "Mos.ru",
    # Афиши и агрегаторы
    "kudago.com":       "KudaGo",
    "afisha.ru":        "Афиша",
    "afishagoroda.ru":  "Афиша Города",
    "eatout.ru":        "EatOut",
    "kidsreview.ru":    "KidsReview",
    "detinform.ru":     "Детинформ",
    "prazdnik.vkusvill.ru": "ВкусВилл Праздник",
    # Квест-агрегаторы
    "topkvestov.ru":    "ТопКвестов",
    "kvestinfo.ru":     "КвестИнфо",
    "mir-kvestov.ru":   "Мир Квестов",
    "questroom.ru":     "QuestRoom",
    "questbook.ru":     "QuestBook",
    # Туризм
    "tourister.ru":     "Туристер",
}


def _source_name_from_url(url: str | None) -> str:
    """Extract a human-readable site name from a URL.

    Priority:
      1. Exact match in DOMAIN_LABELS (e.g. maps.yandex.ru → Яндекс.Карты)
      2. Base-domain match (e.g. spb.2gis.ru → 2ГИС)
      3. Fallback: capitalised second-level domain (e.g. habr.com → Habr)
    """
    if not url:
        return "Источник"
    try:
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
        if not host:
            return "Источник"

        # 1. Exact match (subdomain included, e.g. maps.yandex.ru)
        if host in DOMAIN_LABELS:
            return DOMAIN_LABELS[host]

        # 2. Base domain match (strip leading subdomains one by one)
        parts = host.split(".")
        for i in range(1, len(parts) - 1):
            base = ".".join(parts[i:])
            if base in DOMAIN_LABELS:
                return DOMAIN_LABELS[base]

        # 3. Capitalise the SLD (e.g. habr.com → Habr, pogruzhenye.ru → Pogruzhenye)
        if len(parts) >= 2:
            return parts[-2].capitalize()

        return host.capitalize()
    except Exception:
        return "Источник"


def _ignore_keyboard(mention_id: str) -> InlineKeyboardMarkup:
    """Inline keyboard with a single 'Not relevant' dismiss button.

    Callback data format: ``ignore:{mention_uuid}``
    Handled by the bot callback router in bot_handler.py.
    Telegram limits callback_data to 64 bytes; UUID (36) + prefix (7) = 43 bytes — safe.
    """
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="❌ нерелевантный результат",
            callback_data=f"ignore:{mention_id}",
        )
    ]])


def _format_pub_date(dt: datetime) -> str:
    """Format a publication datetime.

    If time is exactly 00:00 UTC (Tavily often returns date-only), show only the date.
    Otherwise show date + time (UTC).
    """
    dt_utc = dt.astimezone(timezone.utc)
    if dt_utc.hour == 0 and dt_utc.minute == 0 and dt_utc.second == 0:
        return dt_utc.strftime("%d.%m.%Y")
    return dt_utc.strftime("%d.%m.%Y %H:%M")


def _format_alert(mention: Mention, is_content_update: bool = False) -> str:
    """Format a Telegram alert message for a single mention.

    Structure:
      HEADER (event type + sentiment or "content updated")

      📝 Summary
      ⚡ Reason (if any)

      📡 [Site name](url)  ·  📅 Published  ·  🕐 Added
    """
    sentiment_val = mention.sentiment.value if mention.sentiment else "neutral"
    event_val = mention.event_type.value if mention.event_type else "mention"

    s_emoji = SENTIMENT_EMOJI.get(sentiment_val, "⚪️")
    s_label = SENTIMENT_LABEL.get(sentiment_val, "Нейтрально")
    e_emoji, e_label = EVENT_TYPE_LABEL.get(event_val, ("📌", "Упоминание"))

    # ── Header ───────────────────────────────────────────
    if is_content_update:
        header = f"💬 *Обновление: новые комментарии*\n{e_emoji} {e_label} · {s_emoji} {s_label}"
    else:
        header = f"{e_emoji} *{e_label}* · {s_emoji} {s_label}"

    lines = [header, ""]

    # ── Summary ──────────────────────────────────────────
    if mention.ai_summary:
        lines.append(f"📝 {mention.ai_summary}")

    # ── Reason (cause of positive/negative sentiment) ────
    if mention.ai_reason:
        lines.append(f"⚡ Причина: _{mention.ai_reason}_")

    lines.append("")

    # ── Meta block ───────────────────────────────────────
    # Source: clickable site name (extracted from URL) or plain fallback
    if mention.url:
        site_name = _source_name_from_url(mention.url)
        meta = [f"📡 [{site_name}]({mention.url})"]
    else:
        from src.models import SourceType
        src_fallback = {
            "web": "Веб", "news": "Новости", "vk": "ВКонтакте",
            "yandex_maps": "Яндекс.Карты", "telegram": "Telegram", "other": "Другое",
        }
        src_key = mention.source_type.value if mention.source_type else "other"
        meta = [f"📡 {src_fallback.get(src_key, 'Другое')}"]

    activity_dt = getattr(mention, "activity_published_at", None)
    activity_type = getattr(mention, "activity_type", None)

    # Event date: publication date for articles/posts, comment date for updates.
    if activity_dt:
        if activity_type == "comment":
            meta.append(f"📅 коммент. {_format_pub_date(activity_dt)}")
        else:
            meta.append(f"📅 {_format_pub_date(activity_dt)}")
    elif mention.source_published_at:
        meta.append(f"📅 {_format_pub_date(mention.source_published_at)}")
    else:
        meta.append("📅 дата не указана")

    # Date added to our DB
    added = mention.created_at.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M")
    meta.append(f"🕐 добавлено {added}")

    lines.append("  ·  ".join(meta))

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


async def resolve_telegram_chat_ids(settings: Settings) -> List[str]:
    """Return manual target chat plus chats subscribed through /start."""
    chat_ids: List[str] = []
    if settings.telegram_target_chat_id:
        chat_ids.append(str(settings.telegram_target_chat_id))

    try:
        from src.db import get_active_telegram_chat_ids, get_session
        async for session in get_session():
            chat_ids.extend(await get_active_telegram_chat_ids(session))
    except Exception as e:
        logger.debug("Could not load Telegram subscribers: %s", e)

    seen: Set[str] = set()
    unique: List[str] = []
    for chat_id in chat_ids:
        if chat_id and chat_id not in seen:
            seen.add(chat_id)
            unique.append(chat_id)
    return unique


async def send_alert(settings: Settings, mention: Mention) -> None:
    """Send a Telegram alert for a single mention (opens its own session).

    For bulk sending prefer send_alerts_bulk() to reuse one HTTP connection.
    """
    if not settings.telegram_bot_token:
        logger.debug("Telegram not configured, skipping alert")
        return

    chat_ids = await resolve_telegram_chat_ids(settings)
    if not chat_ids:
        logger.debug("No Telegram target chats configured, skipping alert")
        return

    async with _bot_session(settings) as bot:
        text = _format_alert(mention)
        keyboard = _ignore_keyboard(str(mention.id))
        for chat_id in chat_ids:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
                disable_web_page_preview=False,
            )
        logger.info("Alert sent for %s", mention.id)


def _should_alert(mention: Mention, alert_on_sentiment: str) -> bool:
    """Check if this mention should trigger a Telegram alert based on sentiment filter.

    alert_on_sentiment values:
      "all"              — alert for every mention (default)
      "negative_only"    — only negative mentions
      "negative_neutral" — negative and neutral mentions
    """
    sentiment = mention.sentiment.value if mention.sentiment else "neutral"
    if alert_on_sentiment == "negative_only":
        return sentiment == "negative"
    if alert_on_sentiment == "negative_neutral":
        return sentiment in ("negative", "neutral")
    return True  # "all" or any unknown value


async def send_alerts_bulk(
    settings: Settings,
    mentions: List[Mention],
    content_update_ids: Optional[set] = None,
) -> List[bool]:
    """Send Telegram alerts for multiple mentions over a single HTTP session.

    Applies sentiment filter from settings.alert_on_sentiment.
    Adds a small delay between messages to stay within Telegram rate limits
    (30 messages/second for bots).

    content_update_ids: set of mention UUIDs that are content updates (new
    comments on old pages) — shown with a different header in the alert.

    Returns a list of booleans indicating success for each mention.
    """
    if not settings.telegram_bot_token:
        logger.debug("Telegram not configured, skipping bulk alerts")
        return [False] * len(mentions)

    chat_ids = await resolve_telegram_chat_ids(settings)
    if not chat_ids:
        logger.debug("No Telegram target chats configured, skipping bulk alerts")
        return [False] * len(mentions)

    alert_filter = getattr(settings, "alert_on_sentiment", "all")
    update_ids = content_update_ids or set()
    results: List[bool] = []

    async with _bot_session(settings) as bot:
        for mention in mentions:
            if not _should_alert(mention, alert_filter):
                logger.debug(
                    "Alert skipped (filter=%s, sentiment=%s): %s",
                    alert_filter,
                    mention.sentiment.value if mention.sentiment else "neutral",
                    mention.id,
                )
                results.append(False)
                continue

            try:
                is_update = mention.id in update_ids
                text = _format_alert(mention, is_content_update=is_update)
                keyboard = _ignore_keyboard(str(mention.id))
                sent_ok = True
                for chat_id in chat_ids:
                    try:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=text,
                            reply_markup=keyboard,
                            disable_web_page_preview=False,
                        )
                    except Exception as chat_err:
                        sent_ok = False
                        logger.error(
                            "Failed to send alert for %s to chat %s: %s",
                            mention.id, chat_id, chat_err,
                        )
                logger.info("Alert sent for %s", mention.id)
                results.append(sent_ok)
                # Respect Telegram rate limit: max ~30 msg/sec per bot
                await asyncio.sleep(0.05)
            except Exception as e:
                logger.error("Failed to send alert for %s: %s", mention.id, e)
                results.append(False)

    return results
