"""Tests for src/tools/apify.py"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.apify import run_actor, search_vk, get_yandex_maps_reviews


def _mock_client(response):
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


class TestRunActor:
    @pytest.mark.asyncio
    async def test_uses_tilde_actor_ref_and_dataset_endpoint(self):
        start_resp = MagicMock()
        start_resp.raise_for_status = MagicMock()
        start_resp.json.return_value = {"data": {"defaultDatasetId": "dataset-1"}}

        items_resp = MagicMock()
        items_resp.raise_for_status = MagicMock()
        items_resp.json.return_value = [{"text": "ok"}]

        start_client = _mock_client(start_resp)
        start_client.post = AsyncMock(return_value=start_resp)
        fetch_client = _mock_client(items_resp)
        fetch_client.get = AsyncMock(return_value=items_resp)

        with patch("httpx.AsyncClient", side_effect=[start_client, fetch_client]):
            result = await run_actor("apify-key", "user/actor-name", {"query": "brand"})

        assert result == [{"text": "ok"}]
        assert "/acts/user~actor-name/runs" in start_client.post.call_args[0][0]
        assert "/datasets/dataset-1/items" in fetch_client.get.call_args[0][0]
        assert "token" not in start_client.post.call_args[1].get("params", {})
        assert start_client.post.call_args[1]["headers"]["Authorization"] == "Bearer apify-key"


class TestActorInputs:
    @pytest.mark.asyncio
    async def test_search_vk_uses_current_actor_input(self):
        with patch("src.tools.apify.run_actor", new=AsyncMock(return_value=[])) as run:
            await search_vk("key", "TestBrand", max_posts=5)

        assert run.call_args.kwargs["actor_id"] == "endspec/vk-instant-content-scraper"
        assert run.call_args.kwargs["run_input"] == {"query": "TestBrand", "count": 5}

    @pytest.mark.asyncio
    async def test_search_vk_uses_post_actor_for_explicit_targets(self):
        with patch("src.tools.apify.run_actor", new=AsyncMock(return_value=[])) as run:
            await search_vk(
                "key",
                "TestBrand",
                max_posts=5,
                targets=["pogruzhenye.official"],
            )

        assert run.call_args.kwargs["actor_id"] == "maximedupre/vk-posts-scraper"
        assert run.call_args.kwargs["run_input"] == {
            "targets": ["pogruzhenye.official"],
            "maxItems": 5,
            "maxItemsPerTarget": 5,
        }

    @pytest.mark.asyncio
    async def test_yandex_maps_uses_current_actor_input(self):
        with patch("src.tools.apify.run_actor", new=AsyncMock(return_value=[])) as run:
            await get_yandex_maps_reviews("key", "123", max_reviews=7)

        assert run.call_args.kwargs["actor_id"] == "zen-studio/yandex-maps-reviews-scraper"
        assert run.call_args.kwargs["run_input"] == {
            "businessIds": ["123"],
            "maxReviewsPerPlace": 7,
            "sort": "newest",
        }
