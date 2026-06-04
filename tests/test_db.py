
"""Tests for src/db.py"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy.ext.asyncio import AsyncSession
from src.db import url_hash, text_hash, mention_exists_by_url, mention_exists_by_text_hash

class TestHashing:
    def test_url_hash_returns_64_char_hex(self):
        h = url_hash("https://example.com")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_text_hash_returns_64_char_hex(self):
        h = text_hash("Test text")
        assert len(h) == 64

    def test_url_hash_is_deterministic(self):
        url = "https://example.com/article/123"
        assert url_hash(url) == url_hash(url)

    def test_text_hash_is_deterministic(self):
        text = "Same text"
        assert text_hash(text) == text_hash(text)

    def test_different_urls_give_different_hashes(self):
        assert url_hash("https://site-a.com") != url_hash("https://site-b.com")

    def test_different_texts_give_different_hashes(self):
        assert text_hash("text one") != text_hash("text two")

    def test_url_and_text_hash_same_input(self):
        assert url_hash("hello") == text_hash("hello")

    def test_empty_string_hash(self):
        assert len(url_hash("")) == 64

    def test_unicode_hash(self):
        assert len(text_hash("Привет мир")) == 64

class TestMentionExistsByUrl:
    @pytest.mark.asyncio
    async def test_returns_true_when_found(self):
        mock_session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalar.return_value = 1
        mock_session.execute = AsyncMock(return_value=mock_result)
        result = await mention_exists_by_url(mock_session, "https://example.com")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_not_found(self):
        mock_session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalar.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        result = await mention_exists_by_url(mock_session, "https://new.com")
        assert result is False

    @pytest.mark.asyncio
    async def test_uses_url_hash_in_query(self):
        mock_session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalar.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        url = "https://example.com/test"
        await mention_exists_by_url(mock_session, url)
        call_args = mock_session.execute.call_args
        params = call_args[0][1]
        assert params["h"] == url_hash(url)

class TestMentionExistsByTextHash:
    @pytest.mark.asyncio
    async def test_returns_true_when_found(self):
        mock_session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalar.return_value = 1
        mock_session.execute = AsyncMock(return_value=mock_result)
        result = await mention_exists_by_text_hash(mock_session, "Test text")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_not_found(self):
        mock_session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalar.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        result = await mention_exists_by_text_hash(mock_session, "New text")
        assert result is False
