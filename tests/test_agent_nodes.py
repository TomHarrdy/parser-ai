"""Tests for LangGraph pipeline nodes in src/agent.py"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.agent import PipelineState, collect_mentions, deduplicate, analyze_mentions, analyze_with_llm

POS_RESP = json.dumps({"sentiment": "positive", "reason": "good", "summary": "Great."})
MIX_RESP = json.dumps({"sentiment": "mixed", "reason": None, "summary": "Meh."})


class TestCollectMentions:
    @pytest.mark.asyncio
    async def test_collects_tavily_results(self, mock_settings):
        fake = [{"url": "https://s.com/1", "content": "Review", "raw_content": ""},
                {"url": "https://s.com/2", "content": "News", "raw_content": ""}]
        with patch("src.agent.search_brand_mentions", new=AsyncMock(return_value=fake)), \
             patch("src.agent.search_monitoring_phrases", new=AsyncMock(return_value=[])), \
             patch("src.agent.search_vk", new=AsyncMock(return_value=[])), \
             patch("src.agent.get_yandex_maps_reviews", new=AsyncMock(return_value=[])):
            state = PipelineState(settings=mock_settings)
            result = await collect_mentions(state)
        assert len(result.raw_mentions) == 2
        assert result.raw_mentions[0]["source_name"] == "tavily"

    @pytest.mark.asyncio
    async def test_handles_tavily_error(self, mock_settings):
        with patch("src.agent.search_brand_mentions", new=AsyncMock(side_effect=Exception("down"))), \
             patch("src.agent.search_monitoring_phrases", new=AsyncMock(return_value=[])), \
             patch("src.agent.search_vk", new=AsyncMock(return_value=[])), \
             patch("src.agent.get_yandex_maps_reviews", new=AsyncMock(return_value=[])):
            state = PipelineState(settings=mock_settings)
            result = await collect_mentions(state)
        assert len(result.errors) >= 1
        assert "Tavily error" in result.errors[0]

    @pytest.mark.asyncio
    async def test_skips_tavily_when_no_key(self, mock_settings):
        mock_settings.tavily_api_key = ""
        with patch("src.agent.search_brand_mentions", new=AsyncMock()) as mock_s, \
             patch("src.agent.search_monitoring_phrases", new=AsyncMock(return_value=[])), \
             patch("src.agent.search_vk", new=AsyncMock(return_value=[])), \
             patch("src.agent.get_yandex_maps_reviews", new=AsyncMock(return_value=[])):
            await collect_mentions(PipelineState(settings=mock_settings))
        mock_s.assert_not_called()


class TestDeduplicate:
    @pytest.mark.asyncio
    async def test_filters_existing_urls(self, mock_settings):
        state = PipelineState(settings=mock_settings)
        state.raw_mentions = [
            {"url": "https://known.com", "text": "known text"},
            {"url": "https://new.com", "text": "new text"},
        ]
        async def fake_session():
            yield AsyncMock()
        async def exists_url(session, url):
            return url == "https://known.com"
        with patch("src.agent.get_session", new=fake_session), \
             patch("src.agent.mention_exists_by_url", new=exists_url), \
             patch("src.agent.mention_exists_by_text_hash", new=AsyncMock(return_value=False)):
            result = await deduplicate(state)
        assert len(result.new_mentions) == 1
        assert result.new_mentions[0]["url"] == "https://new.com"

    @pytest.mark.asyncio
    async def test_skips_empty_text(self, mock_settings):
        state = PipelineState(settings=mock_settings)
        state.raw_mentions = [
            {"url": "https://a.com", "text": "   "},
            {"url": "https://b.com", "text": "real text"},
        ]
        async def fake_session():
            yield AsyncMock()
        with patch("src.agent.get_session", new=fake_session), \
             patch("src.agent.mention_exists_by_url", new=AsyncMock(return_value=False)), \
             patch("src.agent.mention_exists_by_text_hash", new=AsyncMock(return_value=False)):
            result = await deduplicate(state)
        assert len(result.new_mentions) == 1
        assert result.new_mentions[0]["url"] == "https://b.com"


class TestAnalyzeWithLlm:
    @pytest.mark.asyncio
    async def test_returns_structured_result(self, mock_settings):
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = POS_RESP
        client = AsyncMock()
        client.chat.completions.create = AsyncMock(return_value=resp)
        with patch("openai.AsyncOpenAI", return_value=client):
            result = await analyze_with_llm(mock_settings, "Great service!")
        assert result["sentiment"] == "positive"
        assert result["summary"] == "Great."

    @pytest.mark.asyncio
    async def test_normalizes_unknown_sentiment(self, mock_settings):
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = MIX_RESP
        client = AsyncMock()
        client.chat.completions.create = AsyncMock(return_value=resp)
        with patch("openai.AsyncOpenAI", return_value=client):
            result = await analyze_with_llm(mock_settings, "text")
        assert result["sentiment"] == "neutral"

    @pytest.mark.asyncio
    async def test_raises_without_api_key(self, mock_settings):
        mock_settings.llm_api_key = ""
        with pytest.raises(ValueError, match="LLM_API_KEY is not set"):
            await analyze_with_llm(mock_settings, "text")


class TestAnalyzeMentionsNode:
    @pytest.mark.asyncio
    async def test_uses_firecrawl_for_short_text(self, mock_settings):
        state = PipelineState(settings=mock_settings)
        state.new_mentions = [{"url": "https://art.com", "text": "short", "source_type": "web"}]
        analysis = {"sentiment": "neutral", "reason": None, "summary": "Neutral."}
        with patch("src.agent.deep_read_url", new=AsyncMock(return_value="Full text")) as md, \
             patch("src.agent.analyze_with_llm", new=AsyncMock(return_value=analysis)), \
             patch("src.agent.extract_url", new=AsyncMock(return_value=None)):
            await analyze_mentions(state)
        md.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_firecrawl_for_long_text(self, mock_settings):
        state = PipelineState(settings=mock_settings)
        state.new_mentions = [{"url": "https://art.com", "text": "x" * 500, "source_type": "web"}]
        analysis = {"sentiment": "positive", "reason": None, "summary": "Good."}
        with patch("src.agent.deep_read_url", new=AsyncMock()) as md, \
             patch("src.agent.analyze_with_llm", new=AsyncMock(return_value=analysis)), \
             patch("src.agent.extract_url", new=AsyncMock(return_value=None)):
            await analyze_mentions(state)
        md.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_llm_error(self, mock_settings):
        state = PipelineState(settings=mock_settings)
        state.new_mentions = [{"url": "https://s.com", "text": "x" * 50, "source_type": "web"}]
        with patch("src.agent.analyze_with_llm", new=AsyncMock(side_effect=Exception("LLM fail"))), \
             patch("src.agent.deep_read_url", new=AsyncMock(return_value=None)), \
             patch("src.agent.extract_url", new=AsyncMock(return_value=None)):
            result = await analyze_mentions(state)
        assert result.analyzed_mentions[0]["sentiment"] == "neutral"
        assert len(result.errors) == 1
