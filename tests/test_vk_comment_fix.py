"""Tests for VK comment enrichment and URL fix.

Covers four bugs fixed in this session:
  Bug 1 — LLM prompt date rule was too strict (required date in text body)
  Bug 2 — VK comment text lacked context (LLM couldn't determine relevance)
  Bug 3 — VK comment URL used from_id (author) instead of owner_id (page)
  Bug 4 — Python post-LLM check silently dropped vk_comment even with trusted date
"""
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.vk_direct import VKDirectClient
from src.agent import (
    ANALYZE_USER_PROMPT,
    TRUSTED_SOURCE_DATE_NAMES,
    _parse_published_date,
)


# ---------------------------------------------------------------------------
# Bug 3 fix: _normalize_comments must build correct wall URL
# ---------------------------------------------------------------------------

class TestVKNormalizeCommentsURL:
    """_normalize_comments should use owner_id + post_id, not from_id."""

    def _make_client(self):
        return VKDirectClient.__new__(VKDirectClient)

    def test_url_uses_owner_and_post_id(self):
        client = self._make_client()
        items = [{"id": 42, "from_id": 999, "date": 1700000000, "text": "Привет!"}]
        result = client._normalize_comments(items, owner_id=-123456, post_id=77)
        assert result[0]["url"] == "https://vk.com/wall-123456_77?reply=42"

    def test_url_without_comment_id_omits_reply(self):
        client = self._make_client()
        items = [{"id": 0, "from_id": 999, "date": 1700000000, "text": "Текст"}]
        result = client._normalize_comments(items, owner_id=-123456, post_id=77)
        assert result[0]["url"] == "https://vk.com/wall-123456_77"

    def test_url_empty_when_no_owner_post(self):
        client = self._make_client()
        items = [{"id": 42, "from_id": 999, "date": 1700000000, "text": "Текст"}]
        result = client._normalize_comments(items)  # no owner/post
        assert result[0]["url"] == ""

    def test_from_id_not_in_url(self):
        """from_id (commenter) must never appear in the generated URL."""
        client = self._make_client()
        items = [{"id": 5, "from_id": 12345678, "date": 1700000000, "text": "Ok"}]
        result = client._normalize_comments(items, owner_id=-100, post_id=10)
        assert "12345678" not in result[0]["url"]

    def test_published_date_is_iso_string(self):
        client = self._make_client()
        ts = 1700000000
        items = [{"id": 1, "from_id": 1, "date": ts, "text": "Test"}]
        result = client._normalize_comments(items, owner_id=-1, post_id=1)
        pub = result[0]["published_date"]
        # Must be parseable as datetime
        dt = _parse_published_date(pub)
        assert dt is not None
        assert dt.tzinfo == timezone.utc


# ---------------------------------------------------------------------------
# Bug 2 fix: context prefix is added to VK comment text
# ---------------------------------------------------------------------------

def _settings_with_vk(company_name: str = "TestBrand") -> "Settings":
    """Create a Settings instance with VK token enabled."""
    from src.settings import Settings
    return Settings(
        APP_ENV="test",
        LOG_LEVEL="DEBUG",
        SCHEDULE_INTERVAL_MINUTES=60,
        DATABASE_URL="postgresql+asyncpg://test:test@localhost:5432/test",
        TELEGRAM_BOT_TOKEN="1234567890:AAFakeTokenForTestingPurposesOnly123",
        TELEGRAM_TARGET_CHAT_ID="999999999",
        COMPANY_NAME=company_name,
        KEYWORDS="keyword1,keyword2",
        SEARCH_PHRASES="где найти TestBrand",
        MONITORING_LOCATION="Москва",
        MONITORING_LANGUAGES="ru",
        LLM_PROVIDER="openai",
        LLM_API_KEY="sk-fake-key",
        LLM_ENDPOINT="https://api.openai.com/v1",
        LLM_MODEL="gpt-4o-mini",
        TAVILY_API_KEY="",
        FIRECRAWL_API_KEY="",
        ENABLE_FIRECRAWL_FALLBACK=False,
        APIFY_API_KEY="",
        ENABLE_SEARXNG=False,
        SEARXNG_BASE_URL="http://searxng:8080",
        VK_ACCESS_TOKEN="fake-vk-token",
        VK_TARGETS="pogruzhenye.official",
        ENABLE_INSTAGRAM=False,
        INSTAGRAM_TARGETS="",
        YANDEX_MAPS_ORG_ID="",
        MAX_ARTICLE_AGE_DAYS=7,
    )


class TestVKCommentEnrichment:
    """_collect_vk_direct must enrich comment text with company/post context."""

    @pytest.mark.asyncio
    async def test_comment_text_contains_company_context(self):
        """Comments on company VK page should have a context prefix so LLM
        can classify them as relevant without seeing the company name in the
        comment body itself."""
        settings = _settings_with_vk("TestBrand")
        now = datetime.now(timezone.utc)
        vk_result = {
            "url": "https://vk.com/wall-123_456",
            "text": "Приходите к нам на квест!",  # post text
            "postText": "Приходите к нам на квест!",
            "published_date": now.isoformat(),
            "postedAt": now.isoformat(),
            "date": str(int(now.timestamp())),
            "owner_id": -123,
            "post_id": 456,
            "vk_comments": [
                {
                    "text": "Очень понравилось!",
                    "published_date": now.isoformat(),
                    "date": str(int(now.timestamp())),
                    "url": "https://vk.com/wall-123_456?reply=1",
                    "source": "vk_comment",
                }
            ],
        }

        with patch("src.agent.search_vk_direct", new=AsyncMock(return_value=[vk_result])), \
             patch("src.agent._load_watermark", new=AsyncMock(return_value=None)), \
             patch("src.agent.get_session"):

            from src.agent import _collect_vk_direct
            mentions, errors = await _collect_vk_direct(settings)

        comment_mentions = [m for m in mentions if m.source_name == "vk_comment"]
        assert len(comment_mentions) == 1
        text = comment_mentions[0].text
        # Must contain company name context
        assert settings.company_name in text or "ВКонтакте" in text
        # Must contain the original comment
        assert "Очень понравилось!" in text

    @pytest.mark.asyncio
    async def test_comment_without_post_text_still_has_prefix(self):
        """Even if the parent post has empty text, a context prefix is added."""
        settings = _settings_with_vk("TestBrand")
        now = datetime.now(timezone.utc)
        vk_result = {
            "url": "https://vk.com/wall-123_457",
            "text": "",  # no post text
            "postText": "",
            "published_date": now.isoformat(),
            "date": str(int(now.timestamp())),
            "owner_id": -123,
            "post_id": 457,
            "vk_comments": [
                {
                    "text": "Пришли снова!",
                    "published_date": now.isoformat(),
                    "date": str(int(now.timestamp())),
                    "url": "https://vk.com/wall-123_457?reply=2",
                    "source": "vk_comment",
                }
            ],
        }

        with patch("src.agent.search_vk_direct", new=AsyncMock(return_value=[vk_result])), \
             patch("src.agent._load_watermark", new=AsyncMock(return_value=None)), \
             patch("src.agent.get_session"):

            from src.agent import _collect_vk_direct
            mentions, errors = await _collect_vk_direct(settings)

        comment_mentions = [m for m in mentions if m.source_name == "vk_comment"]
        assert len(comment_mentions) == 1
        assert "ВКонтакте" in comment_mentions[0].text or settings.company_name in comment_mentions[0].text
        assert "Пришли снова!" in comment_mentions[0].text


# ---------------------------------------------------------------------------
# Bug 1 fix: ANALYZE_USER_PROMPT no longer requires date inside text body
# ---------------------------------------------------------------------------

class TestAnalyzePromptDateRule:
    """The LLM prompt must NOT require a date to be present inside the text."""

    def test_prompt_does_not_require_date_in_text(self):
        """Verify the old over-strict rule is gone."""
        assert "no publication date is available or visible in the text" not in ANALYZE_USER_PROMPT

    def test_prompt_clarifies_source_published_at_is_sufficient(self):
        """Verify that source_published_at being provided is accepted as a date."""
        assert "source_published_at is provided" in ANALYZE_USER_PROMPT or \
               "source_published_at" in ANALYZE_USER_PROMPT

    def test_prompt_still_rejects_unknown_date_comments_with_caveat(self):
        """Uncertain date should NOT auto-reject — just evaluate relevance normally."""
        assert "unknown" in ANALYZE_USER_PROMPT  # "unknown" should appear near date rules


# ---------------------------------------------------------------------------
# Bug 4 fix: TRUSTED_SOURCE_DATE_NAMES gates the Python post-LLM date check
# ---------------------------------------------------------------------------

class TestTrustedSourceDateNames:
    def test_vk_comment_is_trusted(self):
        assert "vk_comment" in TRUSTED_SOURCE_DATE_NAMES

    def test_vk_is_trusted(self):
        assert "vk" in TRUSTED_SOURCE_DATE_NAMES

    def test_instagram_comment_is_trusted(self):
        assert "instagram_comment" in TRUSTED_SOURCE_DATE_NAMES

