from typing import Optional

import httpx

FIRECRAWL_API_BASE = "https://api.firecrawl.dev"


async def scrape_url(api_key: str, url: str) -> Optional[str]:
    """Deep-scrape a URL to clean Markdown using Firecrawl.

    Returns the page content in Markdown format.
    """
    if not api_key:
        raise ValueError("FIRECRAWL_API_KEY is empty")

    async with httpx.AsyncClient(timeout=60) as client:
        # Start scrape
        resp = await client.post(
            f"{FIRECRAWL_API_BASE}/v1/scrape",
            json={
                "url": url,
                "formats": ["markdown"],
                "onlyMainContent": True,
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("success") and data.get("data"):
            return data["data"].get("markdown", "")
        return None


async def deep_read_url(api_key: str, url: str, max_chars: int = 8000) -> Optional[str]:
    """Scrape a URL and return a truncated version suitable for LLM context."""
    content = await scrape_url(api_key, url)
    if content and len(content) > max_chars:
        content = content[:max_chars] + "\n\n[... truncated]"
    return content
