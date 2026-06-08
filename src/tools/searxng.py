"""SearXNG search adapter.

SearXNG is a free/self-hosted metasearch fallback for web mentions. Its JSON
API must be enabled in the SearXNG settings.
"""

from __future__ import annotations

from typing import List, Optional

import httpx

from src.tools.retry import with_retry


async def search_searxng(
    base_url: str,
    query: str,
    max_results: int = 20,
    language: str = "ru",
) -> List[dict]:
    if not base_url:
        return []

    async def _request() -> List[dict]:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(
                f"{base_url.rstrip('/')}/search",
                params={
                    "q": query,
                    "format": "json",
                    "language": language,
                    "safesearch": 0,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        results: List[dict] = []
        for item in (data.get("results") or [])[:max_results]:
            url = item.get("url")
            if not url:
                continue
            content = item.get("content") or item.get("snippet") or ""
            published = (
                item.get("publishedDate")
                or item.get("published_date")
                or item.get("date")
            )
            results.append({
                "title": item.get("title") or "",
                "url": url,
                "content": content,
                "raw_content": content,
                "published_date": published,
                "score": item.get("score"),
                "engine": item.get("engine"),
            })
        return results

    return await with_retry(_request, label=f"searxng search: {query[:60]}")


async def search_searxng_brand_mentions(
    base_url: str,
    brand: str,
    keywords: List[str],
    location: str = "Москва",
    max_results: int = 20,
    keyword_batch_size: int = 4,
    language: str = "ru",
) -> List[dict]:
    queries: List[str] = []
    if keywords:
        for i in range(0, len(keywords), keyword_batch_size):
            batch = keywords[i : i + keyword_batch_size]
            kw_part = " OR ".join(f'"{k}"' for k in batch)
            query = f'"{brand}" ({kw_part})'
            if location:
                query += f" {location}"
            queries.append(query)
    else:
        query = f'"{brand}"'
        if location:
            query += f" {location}"
        queries.append(query)

    per_query = max(5, max_results // max(len(queries), 1))
    all_results: List[dict] = []
    seen_urls: set[str] = set()
    for query in queries:
        results = await search_searxng(
            base_url,
            query,
            max_results=per_query,
            language=language,
        )
        for item in results:
            url = item.get("url")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_results.append(item)
    return all_results


async def search_searxng_monitoring_phrases(
    base_url: str,
    phrases: List[str],
    location: str = "Москва",
    max_results: int = 5,
    brand: Optional[str] = None,
    language: str = "ru",
) -> List[dict]:
    all_results: List[dict] = []
    seen_urls: set[str] = set()
    for phrase in phrases:
        query = f'"{brand}" {phrase}' if brand else phrase
        if location:
            query += f" {location}"
        try:
            results = await search_searxng(
                base_url,
                query,
                max_results=max_results,
                language=language,
            )
        except Exception:
            continue
        for item in results:
            url = item.get("url")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_results.append(item)
    return all_results
