
"""Tests for src/tools/tavily.py"""
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from src.tools.tavily import search_tavily, search_brand_mentions, search_monitoring_phrases, extract_url

FAKE_RESULTS = [
    {"title": "Review", "url": "https://review.ru/1", "content": "Great!", "raw_content": "Great service!", "score": 0.95},
    {"title": "News", "url": "https://news.ru/1", "content": "News item.", "raw_content": "Full news.", "score": 0.88},
]

def _mock_client(json_data):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = json_data
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(return_value=resp)
    return client

class TestSearchTavily:
    @pytest.mark.asyncio
    async def test_returns_results(self):
        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client({"results": FAKE_RESULTS})
            results = await search_tavily("tvly-fake", "TestBrand")
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_returns_empty_on_no_results(self):
        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client({"results": []})
            results = await search_tavily("tvly-fake", "xyz")
        assert results == []

    @pytest.mark.asyncio
    async def test_raises_on_empty_api_key(self):
        with pytest.raises(ValueError, match="TAVILY_API_KEY is empty"):
            await search_tavily("", "TestBrand")

    @pytest.mark.asyncio
    async def test_passes_days_param(self):
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = _mock_client({"results": []})
            mock_cls.return_value = mock_client
            await search_tavily("tvly-fake", "test", days=3)
            kw = mock_client.post.call_args[1]
            assert kw["json"]["days"] == 3

class TestSearchBrandMentions:
    @pytest.mark.asyncio
    async def test_builds_query_with_brand(self):
        with patch("src.tools.tavily.search_tavily", new=AsyncMock(return_value=[])) as mock_s:
            await search_brand_mentions("tvly-fake", "TestBrand", ["kw1", "kw2"], "Moscow")
            query = mock_s.call_args[0][1]
            assert '"TestBrand"' in query
            assert '"kw1"' in query
            assert "Moscow" in query

    @pytest.mark.asyncio
    async def test_works_without_keywords(self):
        with patch("src.tools.tavily.search_tavily", new=AsyncMock(return_value=[])) as mock_s:
            await search_brand_mentions("tvly-fake", "TestBrand", [], "Moscow")
            query = mock_s.call_args[0][1]
            assert '"TestBrand"' in query

    @pytest.mark.asyncio
    async def test_returns_search_results(self):
        with patch("src.tools.tavily.search_tavily", new=AsyncMock(return_value=FAKE_RESULTS)):
            results = await search_brand_mentions("tvly-fake", "TestBrand", ["kw"])
        assert results == FAKE_RESULTS

class TestSearchMonitoringPhrases:
    @pytest.mark.asyncio
    async def test_searches_each_phrase(self):
        count = 0
        async def fake_search(api_key, query, **kw):
            nonlocal count
            count += 1
            return [{"url": f"https://site.com/{count}", "content": "text"}]
        with patch("src.tools.tavily.search_tavily", new=fake_search):
            await search_monitoring_phrases("tvly-fake", ["phrase1", "phrase2"])
        assert count == 2

    @pytest.mark.asyncio
    async def test_deduplicates_by_url(self):
        shared = [{"url": "https://shared.com", "content": "text"}]
        with patch("src.tools.tavily.search_tavily", new=AsyncMock(return_value=shared)):
            results = await search_monitoring_phrases("tvly-fake", ["p1", "p2"])
        assert len([r for r in results if r["url"] == "https://shared.com"]) == 1

    @pytest.mark.asyncio
    async def test_empty_phrases_returns_empty(self):
        results = await search_monitoring_phrases("tvly-fake", [])
        assert results == []

    @pytest.mark.asyncio
    async def test_continues_on_error(self):
        count = 0
        async def flaky(api_key, query, **kw):
            nonlocal count
            count += 1
            if count == 1:
                raise Exception("fail")
            return [{"url": "https://ok.com", "content": "ok"}]
        with patch("src.tools.tavily.search_tavily", new=flaky):
            results = await search_monitoring_phrases("tvly-fake", ["p1", "p2"])
        assert len(results) == 1

class TestExtractUrl:
    @pytest.mark.asyncio
    async def test_returns_content(self):
        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client({"results": [{"raw_content": "Page text"}]})
            result = await extract_url("tvly-fake", "https://example.com")
        assert result == "Page text"

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_results(self):
        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client({"results": []})
            result = await extract_url("tvly-fake", "https://example.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_key(self):
        assert await extract_url("", "https://example.com") is None
