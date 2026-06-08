"""Tests for LangGraph pipeline nodes in src/agent.py"""
import json
from datetime import datetime, timezone, timedelta
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.agent import (
    PipelineState, collect_mentions, deduplicate, analyze_mentions, analyze_with_llm,
    verify_freshness, _parse_published_date, _is_too_old,
)

POS_RESP = json.dumps({"sentiment": "positive", "reason": "good", "summary": "Great."})
MIX_RESP = json.dumps({"sentiment": "mixed", "reason": None, "summary": "Meh."})


class TestParsedPublishedDate:
    def test_parses_iso_date(self):
        dt = _parse_published_date("2026-05-01")
        assert dt == datetime(2026, 5, 1, tzinfo=timezone.utc)

    def test_parses_iso_datetime(self):
        dt = _parse_published_date("2026-05-01T12:30:00")
        assert dt is not None
        assert dt.year == 2026 and dt.month == 5 and dt.day == 1

    def test_returns_none_for_none(self):
        assert _parse_published_date(None) is None

    def test_returns_none_for_empty_string(self):
        assert _parse_published_date("") is None

    def test_returns_none_for_garbage(self):
        assert _parse_published_date("not-a-date") is None


class TestIsTooOld:
    def test_recent_article_not_too_old(self):
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        assert _is_too_old(yesterday, max_age_days=7) is False

    def test_old_article_is_too_old(self):
        old = datetime.now(timezone.utc) - timedelta(days=30)
        assert _is_too_old(old, max_age_days=7) is True

    def test_none_date_is_never_too_old(self):
        assert _is_too_old(None, max_age_days=7) is False

    def test_exactly_on_boundary_is_too_old(self):
        # strictly less than cutoff
        cutoff_minus_1s = datetime.now(timezone.utc) - timedelta(days=7, seconds=1)
        assert _is_too_old(cutoff_minus_1s, max_age_days=7) is True

    def test_zero_max_age_filters_everything_with_date(self):
        yesterday = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert _is_too_old(yesterday, max_age_days=0) is True


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
        assert "Tavily" in result.errors[0] and "down" in result.errors[0]

    @pytest.mark.asyncio
    async def test_collects_vk_posts_from_explicit_targets(self, mock_settings):
        mock_settings.vk_targets = "pogruzhenye.official"
        fake_vk = [{
            "sourceUrl": "https://vk.com/wall-104950814_8340",
            "text": "Свежий пост Погружение",
            "postedAt": "2026-06-07T20:01:24.000Z",
        }]
        with patch("src.agent.search_brand_mentions", new=AsyncMock(return_value=[])), \
             patch("src.agent.search_monitoring_phrases", new=AsyncMock(return_value=[])), \
             patch("src.agent.search_vk", new=AsyncMock(return_value=fake_vk)) as vk, \
             patch("src.agent.get_yandex_maps_reviews", new=AsyncMock(return_value=[])):
            result = await collect_mentions(PipelineState(settings=mock_settings))

        assert vk.call_args.kwargs["targets"] == ["pogruzhenye.official"]
        assert result.raw_mentions == [{
            "url": "https://vk.com/wall-104950814_8340",
            "text": "Свежий пост Погружение",
            "source_type": "vk",
            "source_name": "vk",
            "published_at": "2026-06-07T20:01:24+00:00",
            "is_content_update": False,
        }]

    @pytest.mark.asyncio
    async def test_skips_apify_vk_when_direct_vk_token_is_configured(self, mock_settings):
        mock_settings.vk_access_token = "vk-token"
        mock_settings.vk_targets = "pogruzhenye.official"
        with patch("src.agent.search_brand_mentions", new=AsyncMock(return_value=[])), \
             patch("src.agent.search_monitoring_phrases", new=AsyncMock(return_value=[])), \
             patch("src.agent.search_vk", new=AsyncMock(return_value=[])) as apify_vk, \
             patch("src.agent.search_vk_direct", new=AsyncMock(return_value=[])) as direct_vk, \
             patch("src.agent.get_yandex_maps_reviews", new=AsyncMock(return_value=[])):
            await collect_mentions(PipelineState(settings=mock_settings))

        apify_vk.assert_not_called()
        direct_vk.assert_called_once()

    @pytest.mark.asyncio
    async def test_filters_old_articles(self, mock_settings):
        """Articles with published_date older than MAX_ARTICLE_AGE_DAYS are dropped."""
        mock_settings.max_article_age_days = 7
        old_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
        fresh_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fake = [
            {"url": "https://old.com", "content": "Old article", "raw_content": "", "published_date": old_date},
            {"url": "https://fresh.com", "content": "Fresh article", "raw_content": "", "published_date": fresh_date},
        ]
        with patch("src.agent.search_brand_mentions", new=AsyncMock(return_value=fake)), \
             patch("src.agent.search_monitoring_phrases", new=AsyncMock(return_value=[])), \
             patch("src.agent.search_vk", new=AsyncMock(return_value=[])), \
             patch("src.agent.get_yandex_maps_reviews", new=AsyncMock(return_value=[])):
            state = PipelineState(settings=mock_settings)
            result = await collect_mentions(state)
        assert len(result.raw_mentions) == 1
        assert result.raw_mentions[0]["url"] == "https://fresh.com"

    @pytest.mark.asyncio
    async def test_filters_old_articles_even_with_stale_watermark(self, mock_settings):
        """MAX_ARTICLE_AGE_DAYS is enforced even if the stored watermark is stale."""
        mock_settings.max_article_age_days = 7
        old_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
        fake = [{"url": "https://old.com", "content": "Old article", "raw_content": "", "published_date": old_date}]
        with patch("src.agent._load_watermark", new=AsyncMock(return_value=datetime.now(timezone.utc) - timedelta(days=365))), \
             patch("src.agent.search_brand_mentions", new=AsyncMock(return_value=fake)), \
             patch("src.agent.search_monitoring_phrases", new=AsyncMock(return_value=[])), \
             patch("src.agent.search_vk", new=AsyncMock(return_value=[])), \
             patch("src.agent.get_yandex_maps_reviews", new=AsyncMock(return_value=[])):
            state = PipelineState(settings=mock_settings)
            result = await collect_mentions(state)
        assert result.raw_mentions == []

    @pytest.mark.asyncio
    async def test_keeps_articles_without_date(self, mock_settings):
        """Articles with no published_date are NOT filtered (we don't know their age)."""
        mock_settings.max_article_age_days = 7
        fake = [{"url": "https://nodatesite.com", "content": "No date", "raw_content": ""}]
        with patch("src.agent.search_brand_mentions", new=AsyncMock(return_value=fake)), \
             patch("src.agent.search_monitoring_phrases", new=AsyncMock(return_value=[])), \
             patch("src.agent.search_vk", new=AsyncMock(return_value=[])), \
             patch("src.agent.get_yandex_maps_reviews", new=AsyncMock(return_value=[])):
            state = PipelineState(settings=mock_settings)
            result = await collect_mentions(state)
        assert len(result.raw_mentions) == 1

    @pytest.mark.asyncio
    async def test_published_at_stored_in_raw_mention(self, mock_settings):
        """published_date from Tavily is forwarded into raw_mentions dict."""
        mock_settings.max_article_age_days = 30
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fake = [{"url": "https://s.com", "content": "Text", "raw_content": "", "published_date": today}]
        with patch("src.agent.search_brand_mentions", new=AsyncMock(return_value=fake)), \
             patch("src.agent.search_monitoring_phrases", new=AsyncMock(return_value=[])), \
             patch("src.agent.search_vk", new=AsyncMock(return_value=[])), \
             patch("src.agent.get_yandex_maps_reviews", new=AsyncMock(return_value=[])):
            result = await collect_mentions(PipelineState(settings=mock_settings))
        assert result.raw_mentions[0]["published_at"] is not None

    @pytest.mark.asyncio
    async def test_collects_searxng_when_enabled(self, mock_settings):
        mock_settings.enable_searxng = True
        mock_settings.searxng_base_url = "http://searxng:8080"
        state = PipelineState(settings=mock_settings)
        with patch("src.agent._load_watermark", new=AsyncMock(return_value=None)), \
             patch("src.agent.search_searxng_brand_mentions", new=AsyncMock(return_value=[{
                 "url": "https://web.example.com",
                 "content": "SearXNG mention",
                 "published_date": "2026-06-08",
             }])), \
             patch("src.agent.search_searxng_monitoring_phrases", new=AsyncMock(return_value=[])), \
             patch("src.agent.search_brand_mentions", new=AsyncMock(return_value=[])), \
             patch("src.agent.search_monitoring_phrases", new=AsyncMock(return_value=[])), \
             patch("src.agent.search_vk", new=AsyncMock(return_value=[])), \
             patch("src.agent.search_vk_direct", new=AsyncMock(return_value=[])), \
             patch("src.agent.get_yandex_maps_reviews", new=AsyncMock(return_value=[])):
            result = await collect_mentions(state)

        assert any(m["source_name"] == "searxng" for m in result.raw_mentions)

    @pytest.mark.asyncio
    async def test_collects_instagram_when_enabled(self, mock_settings):
        mock_settings.enable_instagram = True
        mock_settings.instagram_targets = "pogruzhenye.official"
        state = PipelineState(settings=mock_settings)
        with patch("src.agent._load_watermark", new=AsyncMock(return_value=None)), \
             patch("src.agent.search_brand_mentions", new=AsyncMock(return_value=[])), \
             patch("src.agent.search_monitoring_phrases", new=AsyncMock(return_value=[])), \
             patch("src.agent.search_vk", new=AsyncMock(return_value=[])), \
             patch("src.agent.search_vk_direct", new=AsyncMock(return_value=[])), \
             patch("src.agent.get_yandex_maps_reviews", new=AsyncMock(return_value=[])), \
             patch("src.agent.search_instagram_profiles", new=AsyncMock(return_value=[{
                 "url": "https://www.instagram.com/p/ABC123/",
                 "text": "Instagram mention",
                 "published_date": "2026-06-08T10:00:00+00:00",
                 "source": "instagram",
             }])):
            result = await collect_mentions(state)

        assert any(m["source_name"] == "instagram" for m in result.raw_mentions)

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
             patch("src.agent.is_url_ignored", new=AsyncMock(return_value=False)), \
             patch("src.agent.is_url_stale", new=AsyncMock(return_value=False)), \
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
             patch("src.agent.is_url_ignored", new=AsyncMock(return_value=False)), \
             patch("src.agent.is_url_stale", new=AsyncMock(return_value=False)), \
             patch("src.agent.mention_exists_by_url", new=AsyncMock(return_value=False)), \
             patch("src.agent.mention_exists_by_text_hash", new=AsyncMock(return_value=False)):
            result = await deduplicate(state)
        assert len(result.new_mentions) == 1
        assert result.new_mentions[0]["url"] == "https://b.com"

    @pytest.mark.asyncio
    async def test_skips_blocklisted_url(self, mock_settings):
        """URL in operator blocklist must be silently skipped."""
        state = PipelineState(settings=mock_settings)
        state.raw_mentions = [
            {"url": "https://blocked.com", "text": "some text"},
            {"url": "https://ok.com", "text": "other text"},
        ]
        async def fake_session():
            yield AsyncMock()
        async def fake_is_ignored(session, url):
            return url == "https://blocked.com"
        with patch("src.agent.get_session", new=fake_session), \
             patch("src.agent.is_url_ignored", new=fake_is_ignored), \
             patch("src.agent.is_url_stale", new=AsyncMock(return_value=False)), \
             patch("src.agent.mention_exists_by_url", new=AsyncMock(return_value=False)), \
             patch("src.agent.mention_exists_by_text_hash", new=AsyncMock(return_value=False)):
            result = await deduplicate(state)
        assert len(result.new_mentions) == 1
        assert result.new_mentions[0]["url"] == "https://ok.com"

    @pytest.mark.asyncio
    async def test_skips_stale_url_archive(self, mock_settings):
        state = PipelineState(settings=mock_settings)
        state.raw_mentions = [
            {"url": "https://old.com", "text": "old publication"},
            {"url": "https://fresh.com", "text": "fresh publication"},
        ]
        async def fake_session():
            yield AsyncMock()
        async def fake_is_stale(session, url):
            return url == "https://old.com"
        with patch("src.agent.get_session", new=fake_session), \
             patch("src.agent.is_url_ignored", new=AsyncMock(return_value=False)), \
             patch("src.agent.is_url_stale", new=fake_is_stale), \
             patch("src.agent.mention_exists_by_url", new=AsyncMock(return_value=False)), \
             patch("src.agent.mention_exists_by_text_hash", new=AsyncMock(return_value=False)):
            result = await deduplicate(state)
        assert [m["url"] for m in result.new_mentions] == ["https://fresh.com"]

    @pytest.mark.asyncio
    async def test_allows_content_update_even_when_url_is_stale(self, mock_settings):
        state = PipelineState(settings=mock_settings)
        state.raw_mentions = [{
            "url": "https://old.com",
            "text": "fresh comment",
            "is_content_update": True,
        }]
        async def fake_session():
            yield AsyncMock()
        with patch("src.agent.get_session", new=fake_session), \
             patch("src.agent.is_url_ignored", new=AsyncMock(return_value=False)), \
             patch("src.agent.is_url_stale", new=AsyncMock(return_value=True)), \
             patch("src.agent.mention_exists_by_url", new=AsyncMock(return_value=True)), \
             patch("src.agent.mention_exists_by_text_hash", new=AsyncMock(return_value=False)):
            result = await deduplicate(state)
        assert len(result.new_mentions) == 1
        assert result.new_mentions[0]["is_content_update"] is True


class TestVerifyFreshness:
    @pytest.mark.asyncio
    async def test_verifies_fresh_date_from_source_page(self, mock_settings):
        state = PipelineState(settings=mock_settings)
        state.raw_mentions = [{
            "url": "https://fresh.com/article",
            "text": "short",
            "source_type": "web",
            "source_name": "tavily",
            "published_at": None,
        }]
        fresh_date = datetime.now(timezone.utc) - timedelta(hours=2)
        with patch(
            "src.agent.scrape_url_with_meta",
            new=AsyncMock(return_value=("Full source page text", fresh_date)),
        ), patch("src.agent._archive_for_freshness", new=AsyncMock()) as archive:
            result = await verify_freshness(state)
        assert len(result.raw_mentions) == 1
        mention = result.raw_mentions[0]
        assert mention["freshness_verified"] is True
        assert mention["freshness_status"] == "confirmed_fresh"
        assert mention["published_at"].startswith(fresh_date.date().isoformat())
        archive.assert_not_called()

    @pytest.mark.asyncio
    async def test_trusted_vk_date_skips_page_extraction(self, mock_settings):
        fresh_date = datetime.now(timezone.utc) - timedelta(hours=2)
        state = PipelineState(settings=mock_settings)
        state.raw_mentions = [{
            "url": "https://vk.com/club104950814?w=wall-104950814_8340",
            "text": "short vk post",
            "source_type": "vk",
            "source_name": "vk",
            "published_at": fresh_date.isoformat(),
        }]
        with patch("src.agent.scrape_url_with_meta", new=AsyncMock()) as extract, \
             patch("src.agent._archive_for_freshness", new=AsyncMock()) as archive:
            result = await verify_freshness(state)

        assert len(result.raw_mentions) == 1
        assert result.raw_mentions[0]["published_at"].startswith(fresh_date.date().isoformat())
        extract.assert_not_called()
        archive.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_date_is_skipped_before_dedup(self, mock_settings):
        state = PipelineState(settings=mock_settings)
        state.raw_mentions = [{
            "url": "https://nodate.com/article",
            "text": "Undated page",
            "source_type": "web",
            "source_name": "tavily",
            "published_at": None,
        }]
        with patch(
            "src.agent.scrape_url_with_meta",
            new=AsyncMock(return_value=("Full source page text", None)),
        ), patch("src.agent.extract_url", new=AsyncMock(return_value=None)), \
             patch("src.agent._archive_for_freshness", new=AsyncMock()) as archive:
            result = await verify_freshness(state)
        assert result.raw_mentions == []
        archive.assert_called_once()

    @pytest.mark.asyncio
    async def test_old_date_is_skipped_and_archived(self, mock_settings):
        state = PipelineState(settings=mock_settings)
        old_date = datetime.now(timezone.utc) - timedelta(days=30)
        state.raw_mentions = [{
            "url": "https://old.com/article",
            "text": "Old page",
            "source_type": "web",
            "source_name": "tavily",
            "published_at": None,
        }]
        with patch(
            "src.agent.scrape_url_with_meta",
            new=AsyncMock(return_value=("Full old page text", old_date)),
        ), patch("src.agent._archive_for_freshness", new=AsyncMock()) as archive:
            result = await verify_freshness(state)
        assert result.raw_mentions == []
        archive.assert_called_once()


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
    async def test_prompt_includes_freshness_policy_and_source_date(self, mock_settings):
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = POS_RESP
        client = AsyncMock()
        client.chat.completions.create = AsyncMock(return_value=resp)
        published_at = datetime(2018, 5, 10, tzinfo=timezone.utc)
        with patch("openai.AsyncOpenAI", return_value=client):
            await analyze_with_llm(mock_settings, "Old review", published_at=published_at)
        messages = client.chat.completions.create.call_args.kwargs["messages"]
        user_prompt = messages[1]["content"]
        assert "Date freshness policy" in user_prompt
        assert "Freshness cutoff date" in user_prompt
        assert "Source publication date: 2018-05-10" in user_prompt
        assert "free form" in user_prompt
        assert "concrete event/fact from THIS content only" in user_prompt
        assert "More posts" in user_prompt
        assert "neighboring reviews" in user_prompt

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
        with patch("src.agent.scrape_url_with_meta", new=AsyncMock(return_value=("Full text", None))) as md, \
             patch("src.agent.analyze_with_llm", new=AsyncMock(return_value=analysis)), \
             patch("src.agent.extract_url", new=AsyncMock(return_value=None)):
            await analyze_mentions(state)
        md.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_firecrawl_for_long_text(self, mock_settings):
        state = PipelineState(settings=mock_settings)
        state.new_mentions = [{
            "url": "https://art.com",
            "text": "x" * 500,
            "source_type": "web",
            "published_at": datetime.now(timezone.utc).isoformat(),
        }]
        analysis = {"sentiment": "positive", "reason": None, "summary": "Good."}
        with patch("src.agent.scrape_url_with_meta", new=AsyncMock()) as md, \
             patch("src.agent.analyze_with_llm", new=AsyncMock(return_value=analysis)), \
             patch("src.agent.extract_url", new=AsyncMock(return_value=None)):
            await analyze_mentions(state)
        md.assert_not_called()

    @pytest.mark.asyncio
    async def test_deep_reads_long_text_when_date_missing_and_skips_old_review(self, mock_settings):
        mock_settings.max_article_age_days = 7
        old_date = datetime.now(timezone.utc) - timedelta(days=30)
        state = PipelineState(settings=mock_settings)
        state.new_mentions = [{
            "url": "https://reviews.example.com",
            "text": "Long snippet without source date. " * 20,
            "source_type": "web",
            "published_at": None,
        }]
        with patch("src.agent.scrape_url_with_meta", new=AsyncMock(return_value=("Full review text", old_date))) as md, \
             patch("src.agent.analyze_with_llm", new=AsyncMock()) as llm, \
             patch("src.agent.extract_url", new=AsyncMock(return_value=None)):
            result = await analyze_mentions(state)
        assert result.analyzed_mentions == []
        md.assert_called_once()
        llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_relevant_review_when_source_date_missing(self, mock_settings):
        state = PipelineState(settings=mock_settings)
        state.new_mentions = [{
            "url": "https://example.com/reviews/old",
            "text": "Long review text without visible source date. " * 20,
            "source_type": "web",
            "published_at": None,
        }]
        analysis = {
            "is_relevant": True,
            "event_type": "review",
            "sentiment": "positive",
            "reason": "хороший квест",
            "summary": "Положительный отзыв.",
        }
        with patch("src.agent.scrape_url_with_meta", new=AsyncMock(return_value=("Full review text", None))), \
             patch("src.agent.analyze_with_llm", new=AsyncMock(return_value=analysis)) as llm, \
             patch("src.agent.extract_url", new=AsyncMock(return_value=None)):
            result = await analyze_mentions(state)
        assert result.analyzed_mentions == []
        llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_old_instagram_post_date_from_snippet(self, mock_settings):
        mock_settings.max_article_age_days = 7
        state = PipelineState(settings=mock_settings)
        state.new_mentions = [{
            "url": "https://www.instagram.com/reel/DTU919Yjb9Q",
            "text": (
                "Never miss a post from pogruzhenye.official. "
                "Video by КВЕСТЫ «ПОГРУЖЕНИЕ» on January 10, 2026. "
                "More posts from pogruzhenye.official. "
                "Photo by КВЕСТЫ «ПОГРУЖЕНИЕ» on May 31, 2026. "
            ) * 3,
            "source_type": "web",
            "published_at": None,
        }]
        with patch("src.agent.scrape_url_with_meta", new=AsyncMock()) as firecrawl, \
             patch("src.agent.analyze_with_llm", new=AsyncMock()) as llm, \
             patch("src.agent.extract_url", new=AsyncMock(return_value=None)):
            result = await analyze_mentions(state)
        assert result.analyzed_mentions == []
        firecrawl.assert_not_called()
        llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_old_instagram_post_date_from_meta_fallback(self, mock_settings):
        mock_settings.max_article_age_days = 7
        state = PipelineState(settings=mock_settings)
        state.new_mentions = [{
            "url": "https://www.instagram.com/p/C297sLECu_8",
            "text": "1+1=3 на все квесты в парке развлечений Погружение",
            "source_type": "web",
            "published_at": None,
        }]
        with patch("src.agent.scrape_url_with_meta", new=AsyncMock(return_value=(None, None))), \
             patch(
                 "src.agent.fetch_instagram_meta_date",
                 new=AsyncMock(return_value=datetime(2024, 2, 5, tzinfo=timezone.utc)),
             ) as instagram_meta, \
             patch("src.agent.analyze_with_llm", new=AsyncMock()) as llm, \
             patch("src.agent.extract_url", new=AsyncMock(return_value=None)):
            result = await analyze_mentions(state)
        assert result.analyzed_mentions == []
        instagram_meta.assert_called_once()
        llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_old_article_date_found_by_firecrawl(self, mock_settings):
        mock_settings.max_article_age_days = 7
        old_date = datetime.now(timezone.utc) - timedelta(days=30)
        state = PipelineState(settings=mock_settings)
        state.new_mentions = [{"url": "https://old.com", "text": "short", "source_type": "web"}]
        with patch("src.agent.scrape_url_with_meta", new=AsyncMock(return_value=("Full old text", old_date))), \
             patch("src.agent._archive_for_freshness", new=AsyncMock()) as archive, \
             patch("src.agent.analyze_with_llm", new=AsyncMock()) as llm, \
             patch("src.agent.extract_url", new=AsyncMock(return_value=None)):
            result = await analyze_mentions(state)
        assert result.analyzed_mentions == []
        archive.assert_called_once()
        llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_date_skips_before_llm_and_archives(self, mock_settings):
        state = PipelineState(settings=mock_settings)
        state.new_mentions = [{
            "url": "https://nodate.com",
            "text": "Long article text without date. " * 20,
            "source_type": "web",
            "published_at": None,
        }]
        with patch("src.agent.scrape_url_with_meta", new=AsyncMock(return_value=("Full text", None))), \
             patch("src.agent._archive_for_freshness", new=AsyncMock()) as archive, \
             patch("src.agent.analyze_with_llm", new=AsyncMock()) as llm, \
             patch("src.agent.extract_url", new=AsyncMock(return_value=None)):
            result = await analyze_mentions(state)
        assert result.analyzed_mentions == []
        archive.assert_called_once()
        llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_fresh_content_update_reaches_llm(self, mock_settings):
        state = PipelineState(settings=mock_settings)
        state.new_mentions = [{
            "url": "https://old.com/article",
            "text": "Новый комментарий опубликован сегодня",
            "source_type": "web",
            "published_at": datetime.now(timezone.utc).isoformat(),
            "is_content_update": True,
        }]
        analysis = {
            "is_relevant": True,
            "event_type": "comment",
            "sentiment": "negative",
            "reason": "новый комментарий",
            "summary": "Под старой публикацией появился свежий комментарий.",
        }
        with patch("src.agent.scrape_url_with_meta", new=AsyncMock()), \
             patch("src.agent.analyze_with_llm", new=AsyncMock(return_value=analysis)), \
             patch("src.agent.extract_url", new=AsyncMock(return_value=None)):
            result = await analyze_mentions(state)
        assert len(result.analyzed_mentions) == 1
        mention = result.analyzed_mentions[0]
        assert mention["freshness_status"] == "fresh_comment_on_old_page"
        assert mention["activity_type"] == "comment"

    @pytest.mark.asyncio
    async def test_handles_llm_error(self, mock_settings):
        state = PipelineState(settings=mock_settings)
        state.new_mentions = [{
            "url": "https://s.com",
            "text": "x" * 50,
            "source_type": "web",
            "published_at": datetime.now(timezone.utc).isoformat(),
        }]
        with patch("src.agent.analyze_with_llm", new=AsyncMock(side_effect=Exception("LLM fail"))), \
             patch("src.agent.scrape_url_with_meta", new=AsyncMock(return_value=(None, None))), \
             patch("src.agent.extract_url", new=AsyncMock(return_value=None)):
            result = await analyze_mentions(state)
        assert result.analyzed_mentions[0]["sentiment"] == "neutral"
        assert len(result.errors) == 1
