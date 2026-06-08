"""Shared pytest fixtures for parser-ai tests."""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models import Mention, Sentiment, SourceType
from src.settings import Settings


@pytest.fixture
def mock_settings() -> Settings:
    return Settings(
        APP_ENV="test",
        LOG_LEVEL="DEBUG",
        SCHEDULE_INTERVAL_MINUTES=60,
        DATABASE_URL="postgresql+asyncpg://test:test@localhost:5432/test",
        TELEGRAM_BOT_TOKEN="1234567890:AAFakeTokenForTestingPurposesOnly123",
        TELEGRAM_TARGET_CHAT_ID="999999999",
        COMPANY_NAME="TestBrand",
        KEYWORDS="keyword1,keyword2,keyword3",
        SEARCH_PHRASES="где найти TestBrand,отзывы TestBrand",
        MONITORING_LOCATION="Москва",
        MONITORING_LANGUAGES="ru",
        LLM_PROVIDER="openai",
        LLM_API_KEY="sk-fake-key-for-testing",
        LLM_ENDPOINT="https://api.openai.com/v1",
        LLM_MODEL="gpt-4o-mini",
        TAVILY_API_KEY="tvly-fake-key-for-testing",
        FIRECRAWL_API_KEY="fc-fake-key-for-testing",
        ENABLE_FIRECRAWL_FALLBACK=False,
        APIFY_API_KEY="apify-fake-key-for-testing",
        ENABLE_SEARXNG=False,
        SEARXNG_BASE_URL="http://searxng:8080",
        VK_ACCESS_TOKEN="",
        VK_TARGETS="",
        ENABLE_INSTAGRAM=False,
        INSTAGRAM_TARGETS="",
        YANDEX_MAPS_ORG_ID="",
        MAX_ARTICLE_AGE_DAYS=7,
    )


@pytest.fixture
def sample_mention() -> Mention:
    return Mention(
        id=uuid.uuid4(),
        url="https://example.com/review/123",
        url_hash="a" * 64,
        source_type=SourceType.web,
        raw_text="Отличный сервис, очень понравилось!",
        raw_text_hash="b" * 64,
        ai_summary="Положительный отзыв о сервисе компании.",
        sentiment=Sentiment.positive,
        is_alert_sent=False,
        created_at=datetime(2026, 6, 4, 19, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def negative_mention() -> Mention:
    return Mention(
        id=uuid.uuid4(),
        url="https://otzovik.com/review/456",
        url_hash="c" * 64,
        source_type=SourceType.yandex_maps,
        raw_text="Ужасное обслуживание, долго ждали.",
        raw_text_hash="d" * 64,
        ai_summary="Негативный отзыв: долгое ожидание и плохое обслуживание.",
        sentiment=Sentiment.negative,
        is_alert_sent=False,
        created_at=datetime(2026, 6, 4, 20, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def neutral_mention() -> Mention:
    return Mention(
        id=uuid.uuid4(),
        url=None,
        url_hash="e" * 64,
        source_type=SourceType.vk,
        raw_text="Видел упоминание TestBrand в новостях.",
        raw_text_hash="f" * 64,
        ai_summary="Нейтральное упоминание бренда в новостях.",
        sentiment=Sentiment.neutral,
        is_alert_sent=False,
        created_at=datetime(2026, 6, 4, 21, 0, 0, tzinfo=timezone.utc),
    )
