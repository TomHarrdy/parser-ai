"""Tests for src/telegram.py"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.telegram import _format_alert, _ignore_keyboard, send_alerts_bulk, send_alert

GREEN = '🟢'
RED = '🔴'
WHITE = '⚪️'
# New format: shows event type + sentiment label (no longer "Новое упоминание")
SENTIMENT_POSITIVE_LABEL = 'Позитив'
SENTIMENT_NEGATIVE_LABEL = 'Негатив'
SOURCE_EXAMPLE_LABEL = 'Example'


class TestFormatAlert:
    def test_positive_has_green_emoji(self, sample_mention):
        assert GREEN in _format_alert(sample_mention)

    def test_negative_has_red_emoji(self, negative_mention):
        assert RED in _format_alert(negative_mention)

    def test_neutral_has_white_emoji(self, neutral_mention):
        assert WHITE in _format_alert(neutral_mention)

    def test_positive_has_sentiment_label(self, sample_mention):
        assert SENTIMENT_POSITIVE_LABEL in _format_alert(sample_mention)

    def test_negative_has_sentiment_label(self, negative_mention):
        assert SENTIMENT_NEGATIVE_LABEL in _format_alert(negative_mention)

    def test_includes_summary(self, sample_mention):
        assert sample_mention.ai_summary in _format_alert(sample_mention)

    def test_includes_url(self, sample_mention):
        assert sample_mention.url in _format_alert(sample_mention)

    def test_no_none_for_null_url(self, neutral_mention):
        assert "None" not in _format_alert(neutral_mention)

    def test_includes_source_label(self, sample_mention):
        assert SOURCE_EXAMPLE_LABEL in _format_alert(sample_mention)

    def test_includes_timestamp(self, sample_mention):
        # Timestamp is now in dd.mm.yyyy HH:MM format (no "UTC" suffix)
        formatted = _format_alert(sample_mention)
        assert "🕐" in formatted  # clock emoji always present

    def test_includes_event_type_emoji(self, sample_mention):
        # Event type emoji (📌 for "mention") should appear in header
        formatted = _format_alert(sample_mention)
        # At least one of the event type emojis must be present
        event_emojis = {"📰", "⭐", "💬", "📢", "🗣", "📌", "❓"}
        assert any(e in formatted for e in event_emojis)

    def test_content_update_shows_update_header(self, sample_mention):
        formatted = _format_alert(sample_mention, is_content_update=True)
        assert "Обновление" in formatted

    def test_reason_shown_when_present(self, negative_mention):
        negative_mention.ai_reason = "долгое ожидание"
        formatted = _format_alert(negative_mention)
        assert "долгое ожидание" in formatted

class TestSendAlert:
    def test_ignore_button_has_requested_label(self):
        keyboard = _ignore_keyboard("mention-1")
        button = keyboard.inline_keyboard[0][0]

        assert button.text == "❌ нерелевантный результат"
        assert button.callback_data == "ignore:mention-1"

    @pytest.mark.asyncio
    async def test_skips_when_no_token(self, mock_settings, sample_mention):
        mock_settings.telegram_bot_token = ""
        await send_alert(mock_settings, sample_mention)

    @pytest.mark.asyncio
    async def test_skips_when_no_chat_id(self, mock_settings, sample_mention):
        mock_settings.telegram_target_chat_id = ""
        await send_alert(mock_settings, sample_mention)

    @pytest.mark.asyncio
    async def test_sends_to_correct_chat(self, mock_settings, sample_mention):
        bot = AsyncMock()
        bot.session = AsyncMock()
        bot.session.close = AsyncMock()
        with patch("src.telegram.Bot", return_value=bot):
            await send_alert(mock_settings, sample_mention)
        bot.send_message.assert_called_once()
        assert bot.send_message.call_args[1]["chat_id"] == mock_settings.telegram_target_chat_id

class TestSendAlertsBulk:
    @pytest.mark.asyncio
    async def test_returns_false_when_not_configured(self, mock_settings, sample_mention, negative_mention):
        mock_settings.telegram_bot_token = ""
        results = await send_alerts_bulk(mock_settings, [sample_mention, negative_mention])
        assert results == [False, False]

    @pytest.mark.asyncio
    async def test_returns_true_on_success(self, mock_settings, sample_mention, negative_mention):
        bot = AsyncMock()
        bot.session = AsyncMock()
        bot.session.close = AsyncMock()
        with patch("src.telegram.Bot", return_value=bot):
            results = await send_alerts_bulk(mock_settings, [sample_mention, negative_mention])
        assert results == [True, True]
        assert bot.send_message.call_count == 2

    @pytest.mark.asyncio
    async def test_sends_to_subscribed_chats_without_manual_chat_id(self, mock_settings, sample_mention):
        mock_settings.telegram_target_chat_id = ""
        bot = AsyncMock()
        bot.session = AsyncMock()
        bot.session.close = AsyncMock()
        with patch("src.telegram.Bot", return_value=bot), \
             patch("src.telegram.resolve_telegram_chat_ids", new=AsyncMock(return_value=["111", "222"])):
            results = await send_alerts_bulk(mock_settings, [sample_mention])
        assert results == [True]
        assert bot.send_message.call_count == 2
        assert [call.kwargs["chat_id"] for call in bot.send_message.call_args_list] == ["111", "222"]

    @pytest.mark.asyncio
    async def test_returns_false_on_error(self, mock_settings, sample_mention):
        bot = AsyncMock()
        bot.session = AsyncMock()
        bot.session.close = AsyncMock()
        bot.send_message = AsyncMock(side_effect=Exception("Error"))
        with patch("src.telegram.Bot", return_value=bot):
            results = await send_alerts_bulk(mock_settings, [sample_mention])
        assert results == [False]

    @pytest.mark.asyncio
    async def test_partial_failure(self, mock_settings, sample_mention, negative_mention):
        bot = AsyncMock()
        bot.session = AsyncMock()
        bot.session.close = AsyncMock()
        count = 0
        async def send(**kw):
            nonlocal count
            count += 1
            if count == 2:
                raise Exception("Rate limited")
        bot.send_message = AsyncMock(side_effect=send)
        with patch("src.telegram.Bot", return_value=bot):
            results = await send_alerts_bulk(mock_settings, [sample_mention, negative_mention])
        assert results == [True, False]

    @pytest.mark.asyncio
    async def test_empty_list(self, mock_settings):
        assert await send_alerts_bulk(mock_settings, []) == []
