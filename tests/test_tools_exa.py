"""Tests for src/tools/exa.py"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.exa import (
    extract_exa_url,
    search_exa,
    search_exa_brand_mentions,
    search_exa_monitoring_phrases,
)


def _mock_client(json_data):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = json_data
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(return_value=resp)
    return client


@pytest.mark.asyncio
async def test_search_exa_normalizes_results():
    payload = {
        "results": [{
            "title": "Fresh review",
            "url": "https://example.com/review",
            "highlights": ["Погружение отзыв"],
            "text": "Full text",
            "publishedDate": "2026-06-18T10:00:00Z",
            "score": 0.91,
        }]
    }
    with patch("httpx.AsyncClient", return_value=_mock_client(payload)) as cls:
        results = await search_exa("exa-key", "Погружение", search_type="fast", max_age_hours=6)

    assert results[0]["url"] == "https://example.com/review"
    assert results[0]["content"] == "Погружение отзыв"
    assert results[0]["raw_content"] == "Full text"
    assert results[0]["published_date"] == "2026-06-18T10:00:00Z"
    body = cls.return_value.post.call_args.kwargs["json"]
    assert body["type"] == "fast"
    assert body["contents"]["maxAgeHours"] == 6


@pytest.mark.asyncio
async def test_search_exa_raises_on_empty_key():
    with pytest.raises(ValueError, match="EXA_API_KEY is empty"):
        await search_exa("", "query")


@pytest.mark.asyncio
async def test_brand_mentions_deduplicates_urls():
    items = [
        {"url": "https://same.com", "content": "one"},
        {"url": "https://same.com", "content": "two"},
    ]
    with patch("src.tools.exa.search_exa", new=AsyncMock(return_value=items)):
        results = await search_exa_brand_mentions("exa-key", "Brand", ["kw1", "kw2"])
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

    with patch("src.tools.exa.search_exa", new=fake_search):
        results = await search_exa_monitoring_phrases("exa-key", ["bad", "good"])
    assert results == [{"url": "https://ok.com", "content": "ok"}]


@pytest.mark.asyncio
async def test_extract_exa_url_returns_text_and_date():
    payload = {
        "results": [{
            "text": "Extracted page text",
            "publishedDate": "2026-06-18",
        }]
    }
    with patch("httpx.AsyncClient", return_value=_mock_client(payload)) as cls:
        text, published = await extract_exa_url("exa-key", "https://example.com", max_age_hours=12)

    assert text == "Extracted page text"
    assert published == "2026-06-18"
    body = cls.return_value.post.call_args.kwargs["json"]
    assert body["ids"] == ["https://example.com"]
    assert body["maxAgeHours"] == 12


@pytest.mark.asyncio
async def test_extract_exa_url_empty_key_returns_none():
    assert await extract_exa_url("", "https://example.com") == (None, None)
