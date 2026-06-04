from typing import List, Optional

import httpx

APIFY_API_BASE = "https://api.apify.com/v2"


async def run_actor(
    api_key: str,
    actor_id: str,
    run_input: dict,
    timeout: int = 120,
) -> List[dict]:
    """Run an Apify Actor and return its dataset items.

    Args:
        api_key: Apify API key
        actor_id: Actor ID (e.g. 'apify/vk-search-scraper')
        run_input: Actor-specific input parameters
        timeout: Max seconds to wait for the actor to finish

    Returns list of result items from the actor's default dataset.
    """
    if not api_key:
        raise ValueError("APIFY_API_KEY is empty")

    async with httpx.AsyncClient(timeout=timeout + 30) as client:
        # Start the actor run
        resp = await client.post(
            f"{APIFY_API_BASE}/acts/{actor_id}/runs",
            json=run_input,
            params={"token": api_key, "waitForFinish": timeout},
        )
        resp.raise_for_status()
        data = resp.json()

        run_id = data.get("data", {}).get("id")
        if not run_id:
            return []

        # Fetch dataset items
        items_resp = await client.get(
            f"{APIFY_API_BASE}/acts/{actor_id}/runs/{run_id}/dataset/items",
            params={"token": api_key},
        )
        items_resp.raise_for_status()
        return items_resp.json()


async def search_vk(
    api_key: str,
    query: str,
    max_posts: int = 10,
) -> List[dict]:
    """Search VK for posts mentioning a brand/keyword.

    Uses apify/vk-search-scraper actor.
    """
    return await run_actor(
        api_key=api_key,
        actor_id="apify/vk-search-scraper",
        run_input={
            "searchQuery": query,
            "maxPosts": max_posts,
            "skipPostsWithoutText": True,
        },
    )


async def get_yandex_maps_reviews(
    api_key: str,
    organization_id: str,
    max_reviews: int = 20,
) -> List[dict]:
    """Fetch recent reviews from Yandex Maps for a specific organization.

    Uses apify/yandex-maps-reviews-scraper actor.
    """
    return await run_actor(
        api_key=api_key,
        actor_id="apify/yandex-maps-reviews-scraper",
        run_input={
            "organizationIds": [organization_id],
            "maxReviews": max_reviews,
            "reviewsSort": "newest",
        },
    )
