from typing import List, Optional

import httpx

from src.tools.retry import with_retry

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
    Retries up to 3 times on 429 / 5xx / network errors.
    """
    if not api_key:
        raise ValueError("TAVILY_API_KEY is empty")

    async def _request() -> List[dict]:
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

    return await with_retry(_request, label=f"tavily search: {query[:60]}")


async def search_brand_mentions(
    api_key: str,
    brand: str,
    keywords: List[str],
    location: str = "Москва",
    max_results: int = 20,
    keyword_batch_size: int = 4,
    days: int = 1,
) -> List[dict]:
    """Search for recent brand mentions, scoped to a location.

    Keywords are split into batches to avoid query length limits on Tavily API.
    Each batch runs as a separate search; results are deduplicated by URL.
    """
    all_results: List[dict] = []
    seen_urls: set = set()

    # Build list of queries: one per keyword batch (or just brand if no keywords)
    queries: List[str] = []

    if keywords:
        for i in range(0, len(keywords), keyword_batch_size):
            batch = keywords[i : i + keyword_batch_size]
            kw_part = " OR ".join(f'"{k}"' for k in batch)
            query = f'"{brand}" AND ({kw_part})'
            if location:
                query += f" {location}"
            queries.append(query)
    else:
        query = f'"{brand}"'
        if location:
            query += f" {location}"
        queries.append(query)

    # Spread max_results evenly across queries
    per_query = max(5, max_results // max(len(queries), 1))

    for query in queries:
        try:
            results = await search_tavily(api_key, query, max_results=per_query, days=days)
            for r in results:
                url = r.get("url")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(r)
                elif not url:
                    all_results.append(r)
        except Exception as e:
            # Re-raise with richer context so callers can log it properly
            short_query = repr(query[:120])
            raise RuntimeError(
                f"Tavily brand search failed (query: {short_query}): "
                f"{type(e).__name__}: {e}"
            ) from e

    return all_results


async def search_monitoring_phrases(
    api_key: str,
    phrases: List[str],
    location: str = "Москва",
    max_results: int = 5,
    brand: Optional[str] = None,
    days: int = 1,
) -> List[dict]:
    """Search for each monitoring phrase independently (question-style queries).

    Unlike brand mentions (AND logic), phrases are searched one by one
    to catch where the brand name isn't mentioned but the topic is discussed.

    If `brand` is provided, it is appended to each phrase query to improve
    relevance — results must at least co-occur with the brand name on the page.
    """
    all_results: List[dict] = []
    seen_urls: set = set()

    for phrase in phrases:
        # Append brand to narrow down results to brand-relevant pages
        query = f'"{brand}" {phrase}' if brand else phrase
        try:
            results = await search_tavily(
                api_key, query, max_results=max_results, days=days
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
    """Extract clean content from a URL using Tavily Extract.

    Retries up to 3 times on 429 / 5xx / network errors.
    """
    if not api_key:
        return None

    async def _request() -> Optional[str]:
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

    return await with_retry(_request, label=f"tavily extract: {url[:60]}")
