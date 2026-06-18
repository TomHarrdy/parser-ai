"""Exa search and contents adapter.

Exa is an optional AI-native retrieval provider. The agent still runs all Exa
results through the normal freshness, deduplication and LLM gates.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import httpx

from src.tools.retry import with_retry

EXA_API_BASE = "https://api.exa.ai"


def _headers(api_key: str) -> dict[str, str]:
    return {
        "x-api-key": api_key,
        "Content-Type": "application/json",
    }


def _result_text(item: dict) -> str:
    highlights = item.get("highlights") or []
    if highlights:
        return "\n\n".join(str(h).strip() for h in highlights if str(h).strip())
    return str(item.get("text") or item.get("summary") or item.get("content") or "")


async def search_exa(
    api_key: str,
    query: str,
    max_results: int = 10,
    search_type: str = "auto",
    max_age_hours: int = 24,
) -> List[dict]:
    """Search web with Exa and return normalized result dictionaries."""
    if not api_key:
        raise ValueError("EXA_API_KEY is empty")

    payload = {
        "query": query,
        "type": search_type,
        "numResults": max_results,
        "contents": {
            "highlights": {"numSentences": 3},
            "summary": True,
            "livecrawl": "fallback",
        },
    }
    if max_age_hours >= 0:
        payload["contents"]["maxAgeHours"] = max_age_hours

    async def _request() -> List[dict]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{EXA_API_BASE}/search",
                headers=_headers(api_key),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        results: List[dict] = []
        for item in data.get("results") or []:
            url = item.get("url")
            if not url:
                continue
            results.append({
                "title": item.get("title") or "",
                "url": url,
                "content": _result_text(item),
                "raw_content": item.get("text") or "",
                "published_date": item.get("publishedDate"),
                "score": item.get("score"),
                "provider": "exa",
            })
        return results

    return await with_retry(_request, label=f"exa search: {query[:60]}")


async def search_exa_brand_mentions(
    api_key: str,
    brand: str,
    keywords: List[str],
    location: str = "Москва",
    max_results: int = 20,
    keyword_batch_size: int = 4,
    search_type: str = "auto",
    max_age_hours: int = 24,
) -> List[dict]:
    all_results: List[dict] = []
    seen_urls: set[str] = set()
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
    for query in queries:
        results = await search_exa(
            api_key=api_key,
            query=query,
            max_results=per_query,
            search_type=search_type,
            max_age_hours=max_age_hours,
        )
        for item in results:
            url = item.get("url")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_results.append(item)
    return all_results


async def search_exa_monitoring_phrases(
    api_key: str,
    phrases: List[str],
    location: str = "Москва",
    max_results: int = 5,
    brand: Optional[str] = None,
    search_type: str = "auto",
    max_age_hours: int = 24,
) -> List[dict]:
    all_results: List[dict] = []
    seen_urls: set[str] = set()
    for phrase in phrases:
        query = f'"{brand}" {phrase}' if brand else phrase
        if location:
            query += f" {location}"
        try:
            results = await search_exa(
                api_key=api_key,
                query=query,
                max_results=max_results,
                search_type=search_type,
                max_age_hours=max_age_hours,
            )
        except Exception:
            continue
        for item in results:
            url = item.get("url")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_results.append(item)
    return all_results


async def extract_exa_url(
    api_key: str,
    url: str,
    max_age_hours: int = 24,
) -> Tuple[Optional[str], Optional[str]]:
    """Extract page text and published date from Exa Contents API."""
    if not api_key:
        return None, None

    payload = {
        "ids": [url],
        "text": True,
        "livecrawl": "fallback",
    }
    if max_age_hours >= 0:
        payload["maxAgeHours"] = max_age_hours

    async def _request() -> Tuple[Optional[str], Optional[str]]:
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post(
                f"{EXA_API_BASE}/contents",
                headers=_headers(api_key),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        results = data.get("results") or []
        if not results:
            return None, None
        item = results[0]
        return item.get("text") or None, item.get("publishedDate")

    return await with_retry(_request, label=f"exa contents: {url[:60]}")
