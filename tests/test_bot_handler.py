"""Tests for src/bot_handler.py feedback flow."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src import bot_handler
from src.bot_handler import (
    handle_feedback_plain_text,
    handle_feedback_quick,
    handle_schema_command,
    handle_start,
)


async def _single_session(session):
    yield session


class TestStartOnboarding:
    @pytest.mark.asyncio
    async def test_start_subscribes_chat_and_describes_bot(self):
        session = MagicMock()
        user = SimpleNamespace(
            username="client",
            first_name="Client",
            last_name="User",
        )
        message = SimpleNamespace(
            chat=SimpleNamespace(id=777, type="private", title=None),
            from_user=user,
            answer=AsyncMock(),
        )

        with patch("src.bot_handler.get_session", return_value=_single_session(session)), \
             patch("src.bot_handler.save_telegram_subscriber", new=AsyncMock()) as save, \
             patch("src.bot_handler.get_settings", return_value=SimpleNamespace(company_name="TestBrand")):
            await handle_start(message)

        save.assert_called_once()
        assert save.call_args.kwargs["chat_id"] == 777
        assert save.call_args.kwargs["username"] == "client"
        message.answer.assert_called_once()
        assert "AI Parser Bot подключён" in message.answer.call_args.args[0]
        assert "TestBrand" in message.answer.call_args.args[0]
        assert "/schema" in message.answer.call_args.args[0]


class TestSchemaCommand:
    @pytest.mark.asyncio
    async def test_schema_command_sends_html_document(self, tmp_path):
        schema_file = tmp_path / "ai-parser-architecture.html"
        schema_file.write_text("<html></html>", encoding="utf-8")
        message = SimpleNamespace(
            answer_document=AsyncMock(),
            answer=AsyncMock(),
        )

        with patch("src.bot_handler.SCHEMA_FILE_PATH", schema_file):
            await handle_schema_command(message)

        message.answer_document.assert_called_once()
        document = message.answer_document.call_args.args[0]
        assert str(document.path) == str(schema_file)
        assert document.filename == "ai-parser-architecture.html"
        assert "Схема работы AI Parser" in message.answer_document.call_args.kwargs["caption"]
        message.answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_schema_command_handles_missing_file(self, tmp_path):
        missing_file = tmp_path / "missing.html"
        message = SimpleNamespace(
            answer_document=AsyncMock(),
            answer=AsyncMock(),
        )

        with patch("src.bot_handler.SCHEMA_FILE_PATH", missing_file):
            await handle_schema_command(message)

        message.answer.assert_called_once_with("Файл схемы пока не найден на сервере.")
        message.answer_document.assert_not_called()


class TestFeedbackQuick:
    @pytest.mark.asyncio
    async def test_quick_reason_preserves_mention_for_lesson_extraction(self):
        context_key = (123, 456)
        mention = MagicMock()
        timeout_task = MagicMock()
        bot_handler._pending_feedback.clear()
        bot_handler._pending_text_feedback_by_chat.clear()
        bot_handler._pending_feedback[context_key] = {
            "mention_id": "mention-1",
            "mention": mention,
            "timeout_task": timeout_task,
        }

        message = SimpleNamespace(
            chat=SimpleNamespace(id=123),
            message_id=456,
            edit_text=AsyncMock(),
        )
        callback = SimpleNamespace(
            data="fb:mention-1:topic_mismatch",
            message=message,
            answer=AsyncMock(),
        )

        def fake_create_task(coro):
            coro.close()
            return MagicMock()

        with patch("src.bot_handler._extract_and_save_lesson", new=AsyncMock()) as extract, \
             patch("asyncio.create_task", side_effect=fake_create_task):
            await handle_feedback_quick(callback)

        timeout_task.cancel.assert_called_once()
        assert context_key not in bot_handler._pending_feedback
        extract.assert_called_once()
        assert extract.call_args.kwargs["mention"] is mention
        assert extract.call_args.kwargs["user_explanation"] == "🔍 Не соответствие темы поиска"

    @pytest.mark.asyncio
    async def test_open_reply_prompts_and_plain_text_feedback_is_acknowledged(self):
        context_key = (123, 456)
        mention = MagicMock()
        timeout_task = MagicMock()
        bot_handler._pending_feedback.clear()
        bot_handler._pending_text_feedback_by_chat.clear()
        bot_handler._pending_feedback[context_key] = {
            "mention_id": "mention-1",
            "mention": mention,
            "timeout_task": timeout_task,
        }

        question_message = SimpleNamespace(
            chat=SimpleNamespace(id=123),
            message_id=456,
            answer=AsyncMock(),
        )
        callback = SimpleNamespace(
            data="fb:mention-1:open_reply",
            message=question_message,
            answer=AsyncMock(),
        )

        await handle_feedback_quick(callback)

        assert bot_handler._pending_text_feedback_by_chat[123] == context_key
        question_message.answer.assert_called_once()
        assert "Напишите следующим сообщением" in question_message.answer.call_args.args[0]
        callback.answer.assert_called_once_with("Жду ваш текст")

        text_message = SimpleNamespace(
            chat=SimpleNamespace(id=123),
            text="Это старая статья и не про нас",
            reply_to_message=None,
            reply=AsyncMock(),
        )

        def fake_create_task(coro):
            coro.close()
            return MagicMock()

        with patch("src.bot_handler._extract_and_save_lesson", new=AsyncMock()) as extract, \
             patch("asyncio.create_task", side_effect=fake_create_task):
            await handle_feedback_plain_text(text_message)

        text_message.reply.assert_called_once()
        assert "Записали причину" in text_message.reply.call_args.args[0]
        timeout_task.cancel.assert_called_once()
        assert context_key not in bot_handler._pending_feedback
        assert 123 not in bot_handler._pending_text_feedback_by_chat
        extract.assert_called_once()
        assert extract.call_args.kwargs["mention"] is mention
        assert extract.call_args.kwargs["user_explanation"] == "Это старая статья и не про нас"
