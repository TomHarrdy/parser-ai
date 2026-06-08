from typing import List, Optional

import httpx

from src.tools.retry import with_retry

APIFY_API_BASE = "https://api.apify.com/v2"
VK_SEARCH_ACTOR_ID = "endspec/vk-instant-content-scraper"
VK_POSTS_ACTOR_ID = "maximedupre/vk-posts-scraper"


async def run_actor(
    api_key: str,
    actor_id: str,
    run_input: dict,
    timeout: int = 120,
) -> List[dict]:
    """Run an Apify Actor and return its dataset items.

    Args:
        api_key: Apify API key
        actor_id: Actor ID (e.g. 'endspec/vk-instant-content-scraper')
        run_input: Actor-specific input parameters
        timeout: Max seconds to wait for the actor to finish

    Returns list of result items from the actor's default dataset.
    Retries the start call and the result fetch separately on 429/5xx.
    """
    if not api_key:
        raise ValueError("APIFY_API_KEY is empty")
    auth_headers = {"Authorization": f"Bearer {api_key}"}
    actor_ref = actor_id.replace("/", "~")

    # Step 1: start the actor run (retryable)
    async def _start_run() -> str:
        async with httpx.AsyncClient(timeout=timeout + 30) as client:
            resp = await client.post(
                f"{APIFY_API_BASE}/acts/{actor_ref}/runs",
                json=run_input,
                params={"waitForFinish": timeout},
                headers=auth_headers,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", {}).get("defaultDatasetId", "")

    dataset_id = await with_retry(_start_run, label=f"apify start: {actor_id}")
    if not dataset_id:
        return []

    # Step 2: fetch dataset items (retryable)
    async def _fetch_items() -> List[dict]:
        async with httpx.AsyncClient(timeout=30) as client:
            items_resp = await client.get(
                f"{APIFY_API_BASE}/datasets/{dataset_id}/items",
                headers=auth_headers,
            )
            items_resp.raise_for_status()
            return items_resp.json()

    return await with_retry(_fetch_items, label=f"apify fetch: {actor_id}")


async def search_vk(
    api_key: str,
    query: str,
    max_posts: int = 10,
    targets: Optional[List[str]] = None,
) -> List[dict]:
    """Search VK for posts mentioning a brand/keyword.

    Prefer explicit VK targets because the generic search actor may return
    profiles/communities instead of posts.
    """
    if targets:
        return await run_actor(
            api_key=api_key,
            actor_id=VK_POSTS_ACTOR_ID,
            run_input={
                "targets": targets,
                "maxItems": max_posts,
                "maxItemsPerTarget": max_posts,
            },
        )

    return await run_actor(
        api_key=api_key,
        actor_id=VK_SEARCH_ACTOR_ID,
        run_input={
            "query": query,
            "count": max_posts,
        },
    )


async def get_yandex_maps_reviews(
    api_key: str,
    organization_id: str,
    max_reviews: int = 20,
) -> List[dict]:
    """Fetch recent reviews from Yandex Maps for a specific organization.

    Uses a current Apify Yandex Maps reviews actor.
    """
    return await run_actor(
        api_key=api_key,
        actor_id="zen-studio/yandex-maps-reviews-scraper",
        run_input={
            "businessIds": [organization_id],
            "maxReviewsPerPlace": max_reviews,
            "sort": "newest",
        },
    )
