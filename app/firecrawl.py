"""Firecrawl-backed web tools (self-hosted, loopback, no auth).

Each function returns ``(text, sources)``: the text the model reads as tool output, and
a list of ``{"url", "role"}`` dicts capturing which web sources were involved (so the UI
can list them for credibility voting). Text is truncated to keep token accounting fair.
"""
from __future__ import annotations

import httpx

from .config import FIRECRAWL_URL, SCRAPE_CHARS, SEARCH_SNIPPET_CHARS

Source = dict[str, str]


async def available(client: httpx.AsyncClient) -> bool:
    """Quick reachability probe so the app can degrade gracefully without Firecrawl."""
    if not FIRECRAWL_URL:
        return False
    try:
        r = await client.get(FIRECRAWL_URL, timeout=5)
        return r.status_code < 500
    except Exception:
        return False


def _truncate(text: str | None, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…[truncated]"


async def web_search(
    client: httpx.AsyncClient, query: str, limit: int = 5
) -> tuple[str, list[Source]]:
    """Search the web; return (ranked results with snippets, source URLs).

    Deliberately does NOT scrape each result page — that overloads Firecrawl's browser
    service under concurrent multi-model runs and returns empty results. The model gets
    titles/URLs/snippets and can scrape_url any page it wants to read in depth.
    """
    payload = {"query": query, "limit": max(1, min(int(limit or 5), 10))}
    resp = await client.post(f"{FIRECRAWL_URL}/v1/search", json=payload)
    resp.raise_for_status()
    body = resp.json()
    results = body.get("data") or []
    if not results:
        warn = body.get("warning") or "no results returned"
        return (
            f"Web search returned no results for {query!r} ({warn}). Do not invent an "
            "answer from memory — if you cannot find the information, say so plainly."
        ), []

    parts: list[str] = []
    sources: list[Source] = []
    for i, r in enumerate(results, 1):
        title = r.get("title") or "(untitled)"
        url = r.get("url") or ""
        snippet = _truncate(r.get("description"), SEARCH_SNIPPET_CHARS)
        parts.append(f"[{i}] {title}\nURL: {url}\n{snippet}")
        if url:
            sources.append({"url": url, "role": "search_result"})
    return "\n\n---\n\n".join(parts), sources


async def scrape_url(client: httpx.AsyncClient, url: str) -> tuple[str, list[Source]]:
    """Fetch a single page; return (markdown, [the scraped URL])."""
    payload = {"url": url, "formats": ["markdown"]}
    resp = await client.post(f"{FIRECRAWL_URL}/v1/scrape", json=payload)
    resp.raise_for_status()
    body = resp.json()
    data = body.get("data") or {}
    markdown = data.get("markdown")
    sources: list[Source] = [{"url": url, "role": "scraped"}] if url else []
    if not markdown:
        return f"No content extracted from {url}.", sources
    title = (data.get("metadata") or {}).get("title") or url
    return f"# {title}\nURL: {url}\n\n{_truncate(markdown, SCRAPE_CHARS)}", sources
