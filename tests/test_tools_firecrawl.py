
"""Tests for src/tools/firecrawl.py"""
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from src.tools.firecrawl import scrape_url, deep_read_url

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
