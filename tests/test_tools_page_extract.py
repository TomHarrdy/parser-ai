"""Tests for the free-first page extraction cascade."""
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.page_extract import extract_page, scrape_url_with_meta


@pytest.mark.asyncio
async def test_uses_trafilatura_first(monkeypatch):
    fake_trafilatura = SimpleNamespace(
        fetch_url=MagicMock(return_value="<html>ok</html>"),
        extract=MagicMock(return_value="Local extracted text. 5 июня 2026"),
        extract_metadata=MagicMock(return_value=SimpleNamespace(date="2026-06-05")),
    )
    monkeypatch.setitem(sys.modules, "trafilatura", fake_trafilatura)

    result = await extract_page("https://example.com/article")

    assert result.provider == "trafilatura"
    assert result.content == "Local extracted text. 5 июня 2026"
    assert result.published_at == datetime(2026, 6, 5, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_falls_back_to_jina_reader(monkeypatch):
    fake_trafilatura = SimpleNamespace(
        fetch_url=MagicMock(return_value=None),
        extract=MagicMock(),
        extract_metadata=MagicMock(),
    )
    monkeypatch.setitem(sys.modules, "trafilatura", fake_trafilatura)

    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "data": {
            "title": "Fresh review",
            "content": "Полный текст отзыва",
            "publishedTime": "Fri, 05 Jun 2026 20:00:44 GMT",
        }
    }
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get = AsyncMock(return_value=resp)

    with patch("httpx.AsyncClient", return_value=client):
        result = await extract_page("https://example.com/review")

    assert result.provider == "jina_reader"
    assert result.content == "Полный текст отзыва"
    assert result.published_at == datetime(2026, 6, 5, 20, 0, 44, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_jina_antibot_content_is_not_accepted(monkeypatch):
    fake_trafilatura = SimpleNamespace(
        fetch_url=MagicMock(return_value=None),
        extract=MagicMock(),
        extract_metadata=MagicMock(),
    )
    monkeypatch.setitem(sys.modules, "trafilatura", fake_trafilatura)

    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "data": {
            "title": "У вас большие запросы!",
            "content": "Проверяем, что вы не робот",
        }
    }
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get = AsyncMock(return_value=resp)

    with patch("httpx.AsyncClient", return_value=client), \
         patch("src.tools.page_extract.scrape_url_with_firecrawl_meta", new=AsyncMock()) as firecrawl:
        result = await extract_page("https://vk.com/post")

    assert result.provider == "none"
    assert result.content is None
    firecrawl.assert_not_called()


@pytest.mark.asyncio
async def test_uses_firecrawl_as_last_fallback(monkeypatch):
    fake_trafilatura = SimpleNamespace(
        fetch_url=MagicMock(return_value=None),
        extract=MagicMock(),
        extract_metadata=MagicMock(),
    )
    monkeypatch.setitem(sys.modules, "trafilatura", fake_trafilatura)

    with patch("src.tools.page_extract._extract_with_jina_reader", new=AsyncMock(return_value=SimpleNamespace(content=None, published_at=None, provider="jina_reader"))), \
         patch(
             "src.tools.page_extract.scrape_url_with_firecrawl_meta",
             new=AsyncMock(return_value=("Paid fallback text", datetime(2026, 6, 5, tzinfo=timezone.utc))),
         ) as firecrawl:
        result = await extract_page("https://example.com/hard", firecrawl_api_key="fc-key")

    assert result.provider == "firecrawl"
    assert result.content == "Paid fallback text"
    firecrawl.assert_called_once_with("fc-key", "https://example.com/hard")


@pytest.mark.asyncio
async def test_scrape_url_with_meta_keeps_old_tuple_interface(monkeypatch):
    with patch(
        "src.tools.page_extract.extract_page",
        new=AsyncMock(return_value=SimpleNamespace(content="Content", published_at=None)),
    ):
        content, published_at = await scrape_url_with_meta("", "https://example.com")

    assert content == "Content"
    assert published_at is None
