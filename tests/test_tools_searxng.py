"""Tests for src/tools/searxng.py"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.searxng import (
    search_searxng,
    search_searxng_brand_mentions,
    search_searxng_monitoring_phrases,
)


def _mock_client(json_data):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = json_data
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get = AsyncMock(return_value=resp)
    return client


@pytest.mark.asyncio
async def test_search_searxng_normalizes_results():
    payload = {
        "results": [{
            "title": "Review",
            "url": "https://example.com/review",
            "content": "Отзыв о бренде",
            "publishedDate": "2026-06-08",
            "engine": "duckduckgo",
        }]
    }
    with patch("httpx.AsyncClient", return_value=_mock_client(payload)) as cls:
        results = await search_searxng("http://searxng:8080", "Погружение")

    assert results[0]["url"] == "https://example.com/review"
    assert results[0]["raw_content"] == "Отзыв о бренде"
    assert results[0]["published_date"] == "2026-06-08"
    params = cls.return_value.get.call_args.kwargs["params"]
    assert params["format"] == "json"


@pytest.mark.asyncio
async def test_search_searxng_empty_base_returns_empty():
    assert await search_searxng("", "query") == []


@pytest.mark.asyncio
async def test_brand_mentions_deduplicates_urls():
    items = [
        {"url": "https://same.com", "content": "one"},
        {"url": "https://same.com", "content": "two"},
    ]
    with patch("src.tools.searxng.search_searxng", new=AsyncMock(return_value=items)):
        results = await search_searxng_brand_mentions(
            "http://searxng:8080", "Brand", ["kw1", "kw2"]
        )
    assert len(results) == 1


@pytest.mark.asyncio
async def test_monitoring_phrases_continues_on_error():
    count = 0

    async def fake_search(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 1:
            raise RuntimeError("blocked")
        return [{"url": "https://ok.com", "content": "ok"}]

    with patch("src.tools.searxng.search_searxng", new=fake_search):
        results = await search_searxng_monitoring_phrases(
            "http://searxng:8080", ["bad", "good"]
        )
    assert results == [{"url": "https://ok.com", "content": "ok"}]
