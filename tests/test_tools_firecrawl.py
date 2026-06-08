
"""Tests for src/tools/firecrawl.py"""
import pytest
import httpx
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from src.tools.firecrawl import (
    scrape_url,
    deep_read_url,
    scrape_url_with_meta,
    extract_first_content_date,
    extract_instagram_meta_date,
    _extract_latest_content_date,
)

FAKE_MD = ("# Header\n\nContent about TestBrand.\n") * 50

def _mock_httpx(json_data):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = json_data
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(return_value=resp)
    return client

class TestScrapeUrl:
    @pytest.mark.asyncio
    async def test_returns_markdown_on_success(self):
        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_httpx({"success": True, "data": {"markdown": FAKE_MD}})
            result = await scrape_url("fc-fake", "https://example.com")
        assert result == FAKE_MD

    @pytest.mark.asyncio
    async def test_returns_none_when_success_false(self):
        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_httpx({"success": False})
            result = await scrape_url("fc-fake", "https://example.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_data(self):
        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_httpx({"success": True, "data": None})
            result = await scrape_url("fc-fake", "https://example.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_raises_on_empty_api_key(self):
        with pytest.raises(ValueError, match="FIRECRAWL_API_KEY is empty"):
            await scrape_url("", "https://example.com")

    @pytest.mark.asyncio
    async def test_sends_correct_auth_header(self):
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = _mock_httpx({"success": True, "data": {"markdown": "ok"}})
            mock_cls.return_value = mock_client
            await scrape_url("fc-mykey-123", "https://example.com")
            kw = mock_client.post.call_args[1]
            assert kw["headers"]["Authorization"] == "Bearer fc-mykey-123"

    @pytest.mark.asyncio
    async def test_extracts_content_review_date_when_metadata_missing(self):
        markdown = (
            "Отзыв\n\n"
            "Пользователь посетил квест. 21 мая 2026\n\n"
            "Другой отзыв. 5 июня 2026 в 18:30\n"
        )
        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_httpx({
                "success": True,
                "data": {"markdown": markdown, "metadata": {}},
            })
            content, published_at = await scrape_url_with_meta("fc-fake", "https://example.com")
        assert content == markdown
        assert published_at is not None
        assert published_at.year == 2026
        assert published_at.month == 6
        assert published_at.day == 5
        assert published_at.hour == 18
        assert published_at.minute == 30

    @pytest.mark.asyncio
    async def test_raises_on_http_error(self):
        resp = MagicMock()
        resp.raise_for_status.side_effect = httpx.HTTPStatusError("401", request=MagicMock(), response=MagicMock())
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(return_value=resp)
        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = client
            with pytest.raises(httpx.HTTPStatusError):
                await scrape_url("fc-bad", "https://example.com")

class TestDeepReadUrl:
    @pytest.mark.asyncio
    async def test_returns_content_when_short(self):
        with patch("src.tools.firecrawl.scrape_url", new=AsyncMock(return_value="Short content")):
            result = await deep_read_url("fc-fake", "https://example.com")
        assert result == "Short content"

    @pytest.mark.asyncio
    async def test_truncates_long_content(self):
        long_content = "x" * 10000
        with patch("src.tools.firecrawl.scrape_url", new=AsyncMock(return_value=long_content)):
            result = await deep_read_url("fc-fake", "https://example.com", max_chars=8000)
        assert result is not None
        assert result.endswith("[... truncated]")

    @pytest.mark.asyncio
    async def test_returns_none_when_scrape_none(self):
        with patch("src.tools.firecrawl.scrape_url", new=AsyncMock(return_value=None)):
            result = await deep_read_url("fc-fake", "https://example.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_does_not_truncate_at_exact_limit(self):
        content = "y" * 8000
        with patch("src.tools.firecrawl.scrape_url", new=AsyncMock(return_value=content)):
            result = await deep_read_url("fc-fake", "https://example.com", max_chars=8000)
        assert result == content


class TestExtractLatestContentDate:
    def test_extracts_latest_numeric_and_russian_date(self):
        result = _extract_latest_content_date(
            "01.06.2026 старый отзыв\n"
            "Свежий отзыв опубликован 6 июня 2026 в 12:35"
        )
        assert result == datetime(2026, 6, 6, 12, 35, tzinfo=timezone.utc)

    def test_extracts_english_month_date(self):
        result = _extract_latest_content_date("Video by brand on January 10, 2026.")
        assert result == datetime(2026, 1, 10, tzinfo=timezone.utc)

    def test_extracts_first_date_for_instagram_like_text(self):
        result = extract_first_content_date(
            "Video by brand on January 10, 2026. "
            "More posts from brand. Photo by brand on May 31, 2026."
        )
        assert result == datetime(2026, 1, 10, tzinfo=timezone.utc)

    def test_extracts_instagram_date_from_meta_description(self):
        html = (
            '<meta name="description" content="94 likes, 0 comments - '
            'pogruzhenye.official on February 5, 2024: &quot;Самый страшный '
            'злодей квестов EVER&quot;. " />'
        )
        result = extract_instagram_meta_date(html)
        assert result == datetime(2024, 2, 5, tzinfo=timezone.utc)

    def test_today_is_treated_as_fresh(self):
        result = _extract_latest_content_date("Отзыв опубликован сегодня")
        assert result is not None
        today = datetime.now(timezone.utc).date()
        assert result.date() == today

    @pytest.mark.parametrize("label", ["вчера", "2 дня назад", "давно"])
    def test_relative_old_labels_are_treated_as_old(self, label):
        result = _extract_latest_content_date(f"Отзыв опубликован {label}")
        assert result is not None
        assert result < datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
