"""Telegram bot callback handler.

Runs as a long-polling loop alongside the pipeline scheduler.
Handles the "Not relevant" inline button and the follow-up human feedback dialog.

Flow:
  1. User clicks "❌ Нерелевантный результат"
     → callback_data = "ignore:{mention_uuid}"
     → mark_mention_ignored() in DB (is_ignored=True + URL blocklist)
     → edit original message: remove button, show confirmation
     → send new message with quick-reason inline keyboard (asking WHY)
     → store pending context in _pending_feedback dict

  2a. User clicks a quick-reason button
      → callback_data = "fb:{mention_id}:{category}"
      → extract lesson using user-provided category + LLM
      → edit feedback question message: show "Спасибо, изучим!"
      → remove pending context

  2b. User replies to the feedback question message (free text)
      → message.reply_to_message.message_id in _pending_feedback
      → extract lesson using user's text explanation + LLM
      → reply with "Спасибо, записали!"
      → remove pending context

  3. Timeout fallback (FEEDBACK_TIMEOUT_SECONDS):
     If no response within timeout → LLM auto-analyzes without user explanation
     → pending context removed
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    ForceReply,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from src.db import (
    get_session,
    mark_mention_ignored,
    remove_url_ignore,
    save_feedback_lesson,
    save_stale_url,
    save_telegram_subscriber,
    upsert_comment_watchlist,
)
from src.freshness import extract_comment_snapshot, extract_latest_comment_date, looks_commentable
from src.settings import Settings, get_settings

logger = logging.getLogger(__name__)

router = Router(name="feedback")
SCHEMA_FILE_PATH = Path(__file__).resolve().parent.parent / "docs" / "ai-parser-architecture.html"

# Timeout before auto-extracting lesson without user explanation (seconds)
FEEDBACK_TIMEOUT_SECONDS = 600  # 10 minutes

# Maps (chat_id, message_id) -> {"mention_id": str, "mention": Mention, "timeout_task": Task}
# Stores pending feedback requests waiting for human response
_pending_feedback: dict[tuple[int, int], dict] = {}

# Maps chat_id -> original feedback question context key for "Свой ответ" mode.
# This lets users type the next message normally instead of replying manually.
_pending_text_feedback_by_chat: dict[int, tuple[int, int]] = {}

# Quick-reason categories shown as inline buttons
_QUICK_REASONS: list[tuple[str, str]] = [
    ("🏢 Не наша компания", "not_our_company"),
    ("📅 Старая дата публикации", "old_publication"),
    ("🔍 Не соответствие темы поиска", "topic_mismatch"),
]


@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    """Subscribe the current chat to alerts and show a short product intro."""
    user = message.from_user
    try:
        async for session in get_session():
            await save_telegram_subscriber(
                session=session,
                chat_id=message.chat.id,
                chat_type=getattr(message.chat, "type", None),
                title=getattr(message.chat, "title", None),
                username=getattr(user, "username", None) if user else None,
                first_name=getattr(user, "first_name", None) if user else None,
                last_name=getattr(user, "last_name", None) if user else None,
            )
    except Exception as e:
        logger.error("Failed to subscribe Telegram chat %s: %s", message.chat.id, e)
        await message.answer("Не удалось подключить чат. Попробуйте ещё раз.")
        return

    settings = get_settings()
    company = settings.company_name or "вашей компании"
    await message.answer(
        "*AI Parser Bot подключён*\n\n"
        f"Я мониторю упоминания компании *{company}* в вебе, соцсетях и отзывах, "
        "фильтрую нерелевантные результаты через LLM и присылаю сюда важные находки.\n\n"
        "Если результат не подходит, нажмите кнопку *нерелевантный результат* под уведомлением. "
        "Бот добавит URL в блок-лист и учтёт ваш фидбек в будущей фильтрации.\n\n"
        "Команда /schema отправит файл со схемой работы AI Parser.\n\n"
        "Этот чат уже подключён к уведомлениям.",
        parse_mode=ParseMode.MARKDOWN,
    )


@router.message(Command("schema"))
async def handle_schema_command(message: Message) -> None:
    """Send the AI Parser architecture diagram HTML file to Telegram."""
    if not SCHEMA_FILE_PATH.exists():
        logger.error("Schema file is missing: %s", SCHEMA_FILE_PATH)
        await message.answer("Файл схемы пока не найден на сервере.")
        return

    await message.answer_document(
        FSInputFile(SCHEMA_FILE_PATH, filename="ai-parser-architecture.html"),
        caption=(
            "Схема работы AI Parser.\n"
            "Откройте HTML-файл в браузере, чтобы посмотреть интерактивную SVG-схему."
        ),
    )


def _build_feedback_keyboard(mention_id: str) -> InlineKeyboardMarkup:
    """Build inline keyboard with quick-reason buttons + free-text hint."""
    buttons = []
    for label, category in _QUICK_REASONS:
        buttons.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"fb:{mention_id}:{category}",
            )
        ])
    # Free-text option
    buttons.append([
        InlineKeyboardButton(
            text="✏️ Свой ответ",
            callback_data=f"fb:{mention_id}:open_reply",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ── Handler: "❌ Нерелевантный результат" button ─────────────────────────────

@router.callback_query(lambda c: c.data and c.data.startswith("ignore:"))
async def handle_ignore_callback(callback: CallbackQuery) -> None:
    """Handle the 'Not relevant' button press.

    1. Parse mention_id from callback data.
    2. Mark mention as ignored in DB + add URL to blocklist.
    3. Edit the original message to show confirmation (remove button).
    4. Send a follow-up message asking WHY (with quick-reason keyboard).
    5. Start a timeout task: if no reply in FEEDBACK_TIMEOUT_SECONDS, auto-extract lesson.
    """
    mention_id = callback.data.split(":", 1)[1].strip()
    logger.info("Ignore callback received for mention_id=%s", mention_id)

    mention = None
    try:
        async for session in get_session():
            mention = await mark_mention_ignored(session, mention_id)
    except Exception as e:
        logger.error("DB error while ignoring mention %s: %s", mention_id, e)
        await callback.answer("⚠️ Ошибка при обновлении БД", show_alert=True)
        return

    if mention is None:
        await callback.answer("⚠️ Упоминание не найдено в БД", show_alert=True)
        return

    # Edit original message: remove button, show confirmation
    original_text = callback.message.text or ""
    updated_text = original_text + "\n\n✅ _Помечено как нерелевантное. URL добавлен в блок-лист._"
    try:
        await callback.message.edit_text(
            text=updated_text,
            reply_markup=None,
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.warning("Could not edit message for mention %s: %s", mention_id, e)

    await callback.answer("✅ Помечено как нерелевантное")

    # Send follow-up question with quick-reason buttons
    question_text = (
        "🤔 *Почему этот результат нерелевантен?*\n\n"
        "Выберите причину или ответьте на это сообщение своими словами.\n"
        "_Ваш ответ поможет боту лучше фильтровать контент в будущем._"
    )
    question_msg = await callback.message.answer(
        text=question_text,
        reply_markup=_build_feedback_keyboard(mention_id),
        parse_mode=ParseMode.MARKDOWN,
    )

    # Register pending feedback context
    context_key = (question_msg.chat.id, question_msg.message_id)
    timeout_task = asyncio.create_task(
        _feedback_timeout(context_key, mention_id, mention)
    )
    _pending_feedback[context_key] = {
        "mention_id": mention_id,
        "mention": mention,
        "timeout_task": timeout_task,
    }

    logger.info(
        "Feedback question sent for mention %s (msg_id=%d, chat=%d)",
        mention_id, question_msg.message_id, question_msg.chat.id,
    )


# ── Handler: quick-reason button ─────────────────────────────────────────────

@router.callback_query(lambda c: c.data and c.data.startswith("fb:"))
async def handle_feedback_quick(callback: CallbackQuery) -> None:
    """Handle a quick-reason button click.

    callback_data format: "fb:{mention_id}:{category}"
    Special category "open_reply" just prompts user to reply freely.
    """
    parts = callback.data.split(":", 2)
    if len(parts) != 3:
        await callback.answer("⚠️ Неверный формат данных")
        return

    _, mention_id, category = parts

    # Special case: user wants to type their own reason
    if category == "open_reply":
        context_key = (callback.message.chat.id, callback.message.message_id)
        if context_key in _pending_feedback:
            _pending_text_feedback_by_chat[callback.message.chat.id] = context_key
        await callback.message.answer(
            "Напишите следующим сообщением, почему результат нерелевантен. "
            "Я сохраню объяснение и учту его в будущей фильтрации.",
            reply_markup=ForceReply(
                selective=True,
                input_field_placeholder="Почему это нерелевантно?",
            ),
        )
        await callback.answer("Жду ваш текст")
        return

    # Map category code to human-readable label
    category_labels = {k: v for v, k in _QUICK_REASONS}
    label = category_labels.get(category, category)

    await callback.answer(f"✅ Принято: {label}")

    # Edit the question message: show selected reason, remove keyboard
    try:
        await callback.message.edit_text(
            text=(
                f"✅ *Причина записана:* {label}\n"
                "_Агент обновит правила фильтрации на основе вашего ответа._"
            ),
            reply_markup=None,
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.warning("Could not edit feedback message: %s", e)

    # Cancel timeout task and clear pending context, but keep the mention object.
    context_key = (callback.message.chat.id, callback.message.message_id)
    context = _cancel_pending(context_key)
    mention = context.get("mention") if context else None

    if category == "old_publication" and mention and mention.url:
        try:
            async for session in get_session():
                await remove_url_ignore(session, mention.url)
                await save_stale_url(
                    session=session,
                    url=mention.url,
                    mention_id=mention_id,
                    reason="stale_by_operator",
                    source_published_at=mention.source_published_at,
                    can_recheck_comments=looks_commentable(mention.url, mention.raw_text or ""),
                )
                if looks_commentable(mention.url, mention.raw_text or ""):
                    await upsert_comment_watchlist(
                        session=session,
                        url=mention.url,
                        source_name=mention.source_type.value if mention.source_type else "web",
                        main_published_at=mention.source_published_at,
                        last_comment_at=extract_latest_comment_date(mention.raw_text or ""),
                        comments_snapshot=extract_comment_snapshot(mention.raw_text or ""),
                    )
            logger.info("Mention %s archived as stale by operator", mention_id)
        except Exception as e:
            logger.error("Failed to archive stale URL for mention %s: %s", mention_id, e)

    # Extract and save lesson with user-provided category as explanation
    asyncio.create_task(
        _extract_and_save_lesson(
            mention_id=mention_id,
            mention=mention,
            user_explanation=label,
        )
    )


# ── Handler: free-text reply to feedback question ────────────────────────────

@router.message(F.reply_to_message)
async def handle_feedback_text_reply(message: Message) -> None:
    """Capture free-text replies to the feedback question message.

    Checks if the replied-to message is in _pending_feedback.
    If yes — uses user's text as explanation for lesson extraction.
    """
    if not message.reply_to_message:
        return

    context_key = (message.chat.id, message.reply_to_message.message_id)
    context = _pending_feedback.get(context_key)
    if context is None:
        # Not our feedback question — ignore
        return

    await _process_feedback_text(
        message=message,
        context_key=context_key,
        context=context,
        user_text=(message.text or "").strip(),
        question_message=message.reply_to_message,
    )


@router.message(F.text)
async def handle_feedback_plain_text(message: Message) -> None:
    """Capture the next plain text message after user clicked 'Свой ответ'."""
    if message.reply_to_message:
        return
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        return

    context_key = _pending_text_feedback_by_chat.get(message.chat.id)
    if context_key is None:
        return

    context = _pending_feedback.get(context_key)
    if context is None:
        _pending_text_feedback_by_chat.pop(message.chat.id, None)
        return

    await _process_feedback_text(
        message=message,
        context_key=context_key,
        context=context,
        user_text=text,
        question_message=None,
    )


async def _process_feedback_text(
    message: Message,
    context_key: tuple[int, int],
    context: dict,
    user_text: str,
    question_message: Optional[Message],
) -> None:
    """Acknowledge free-text feedback and start lesson extraction."""
    if not user_text:
        return

    mention_id = context["mention_id"]
    mention = context["mention"]

    logger.info(
        "Free-text feedback received for mention %s: %r",
        mention_id, user_text[:100],
    )

    # Cancel timeout and remove from pending
    _cancel_pending(context_key)

    # Acknowledge user
    await message.reply(
        "✅ *Спасибо!* Записали причину. Агент учтёт это при следующем анализе.",
        parse_mode=ParseMode.MARKDOWN,
    )

    # Edit original question message to show it's been answered
    if question_message:
        try:
            await question_message.edit_text(
                text=(
                    "✅ *Обратная связь получена*\n"
                    f"_Причина: {user_text[:200]}_\n\n"
                    "_Агент обновит правила фильтрации._"
                ),
                reply_markup=None,
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            logger.warning("Could not edit feedback question message: %s", e)

    # Extract and save lesson with user's explanation
    asyncio.create_task(
        _extract_and_save_lesson(
            mention_id=mention_id,
            mention=mention,
            user_explanation=user_text,
        )
    )


# ── Internal helpers ──────────────────────────────────────────────────────────

def _cancel_pending(context_key: tuple[int, int]) -> Optional[dict]:
    """Cancel timeout task, remove pending context entry, and return it."""
    context = _pending_feedback.pop(context_key, None)
    for chat_id, pending_key in list(_pending_text_feedback_by_chat.items()):
        if pending_key == context_key:
            _pending_text_feedback_by_chat.pop(chat_id, None)
    if context and context.get("timeout_task"):
        context["timeout_task"].cancel()
    return context


async def _feedback_timeout(
    context_key: tuple[int, int],
    mention_id: str,
    mention,
) -> None:
    """Fallback: if operator doesn't respond within timeout, auto-extract lesson via LLM.

    Runs as a background task. Cancellation = operator responded, task can stop.
    """
    try:
        await asyncio.sleep(FEEDBACK_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        # Operator responded — timeout no longer needed
        return

    # Still pending after timeout
    context = _pending_feedback.pop(context_key, None)
    for chat_id, pending_key in list(_pending_text_feedback_by_chat.items()):
        if pending_key == context_key:
            _pending_text_feedback_by_chat.pop(chat_id, None)
    if context is None:
        return  # Was already processed

    logger.info(
        "Feedback timeout for mention %s — extracting lesson without user explanation",
        mention_id,
    )
    await _extract_and_save_lesson(
        mention_id=mention_id,
        mention=mention,
        user_explanation=None,
    )


async def _extract_and_save_lesson(
    mention_id: str,
    mention,
    user_explanation: Optional[str] = None,
) -> None:
    """Background task: analyze why the mention was a false positive, save lesson to DB.

    When user_explanation is provided — LLM produces a much more accurate lesson.
    When None — LLM infers the reason from the mention text alone (fallback).

    Failures here are logged but never propagated (best-effort).
    """
    from src.llm_feedback import analyze_false_positive

    settings = get_settings()
    source_name = mention.source_type.value if mention and mention.source_type else None
    url = mention.url if mention else None
    raw_text = mention.raw_text if mention else None

    try:
        lesson_data = await analyze_false_positive(
            settings=settings,
            mention_id=mention_id,
            url=url,
            source_name=source_name,
            text=raw_text,
            user_explanation=user_explanation,
        )
        if lesson_data:
            async for session in get_session():
                saved = await save_feedback_lesson(
                    session=session,
                    mention_id=mention_id,
                    url=url,
                    source_name=source_name,
                    mention_snippet=(raw_text or "")[:500],
                    error_category=lesson_data["error_category"],
                    lesson_text=lesson_data["lesson_text"],
                    user_explanation=user_explanation,
                )
                logger.info(
                    "Lesson #%d saved [%s] (user_explanation=%s): %s",
                    saved.id,
                    saved.error_category,
                    "yes" if user_explanation else "no (auto)",
                    saved.lesson_text[:80],
                )
    except Exception as e:
        logger.error("Failed to extract/save lesson for mention %s: %s", mention_id, e)


# ── Entry point ───────────────────────────────────────────────────────────────

async def start_polling(settings: Settings) -> None:
    """Start the bot in long-polling mode.

    Designed to run concurrently with the pipeline scheduler via asyncio.gather().
    Exits cleanly when the task is cancelled (e.g. on SIGTERM).
    """
    if not settings.telegram_bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN not set — bot polling disabled")
        return

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )
    dp = Dispatcher()
    dp.include_router(router)

    logger.info("Starting Telegram bot polling...")
    try:
        await dp.start_polling(bot, handle_signals=False)
    finally:
        await bot.session.close()
        logger.info("Bot polling stopped")
