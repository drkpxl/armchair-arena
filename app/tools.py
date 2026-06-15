"""Tool schemas + dispatch shared identically by every model (tool_choice=auto)."""
from __future__ import annotations

from typing import Any

import httpx

from . import firecrawl

# OpenAI-style function schemas (native Ollama /api/chat accepts the same shape).
# Kept free of `pattern`/`format` keywords, which some Ollama-served models reject.
TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for current or factual information. Returns ranked "
                "results with titles, URLs, and markdown snippets."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query."},
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (1-10, default 5).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scrape_url",
            "description": (
                "Fetch the full content of a specific web page as markdown. Use after "
                "web_search to read a promising result in depth."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The page URL to fetch."},
                },
                "required": ["url"],
            },
        },
    },
]


async def dispatch(
    name: str, args: dict[str, Any], client: httpx.AsyncClient
) -> tuple[str, list[dict[str, str]]]:
    """Execute a tool call; return (text result, source URLs touched)."""
    args = args or {}
    if name == "web_search":
        return await firecrawl.web_search(
            client, query=args.get("query", ""), limit=args.get("limit", 5)
        )
    if name == "scrape_url":
        return await firecrawl.scrape_url(client, url=args.get("url", ""))
    return f"Unknown tool: {name}", []
