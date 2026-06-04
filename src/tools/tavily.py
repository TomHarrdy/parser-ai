from typing import List, Optional

import httpx

TAVILY_API_BASE = "https://api.tavily.com"


async def search_tavily(
    api_key: str,
    query: str,
    max_results: int = 20,
    search_depth: str = "basic",
    days: int = 1,
) -> List[dict]:
    """Search the web using Tavily Search API.

    Returns list of results, each with: title, url, content, raw_content, score.
    """
    if not api_key:
        raise ValueError("TAVILY_API_KEY is empty")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{TAVILY_API_BASE}/search",
            json={
                "api_key": api_key,
                "query": query,
                "max_results": max_results,
                "search_depth": search_depth,
                "days": days,
                "include_raw_content": True,
                "include_answer": False,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", [])


async def search_brand_mentions(
    api_key: str,
    brand: str,
    keywords: List[str],
    location: str = "Москва",
    max_results: int = 20,
) -> List[dict]:
    """Search for recent brand mentions, scoped to a location."""
    query_parts = [f'"{brand}"']
    if keywords:
        query_parts.append("(" + " OR ".join(f'"{k}"' for k in keywords) + ")")
    query = " AND ".join(query_parts)

    if location:
        query += f" {location}"

    return await search_tavily(api_key, query, max_results=max_results, days=1)


async def search_monitoring_phrases(
    api_key: str,
    phrases: List[str],
    location: str = "Москва",
    max_results: int = 5,
) -> List[dict]:
    """Search for each monitoring phrase independently (question-style queries).

    Unlike brand mentions (AND logic), phrases are searched one by one
    to catch where the brand name isn't mentioned but the topic is discussed.
    """
    all_results: List[dict] = []
    seen_urls: set = set()

    for phrase in phrases:
        query = phrase
        try:
            results = await search_tavily(
                api_key, query, max_results=max_results, days=1
            )
            for r in results:
                url = r.get("url")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(r)
        except Exception:
            continue

    return all_results


async def extract_url(api_key: str, url: str) -> Optional[str]:
    """Extract clean content from a URL using Tavily Extract."""
    if not api_key:
        return None

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{TAVILY_API_BASE}/extract",
            json={
                "api_key": api_key,
                "urls": [url],
                "include_images": False,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if results:
            return results[0].get("raw_content", "")
        return None
