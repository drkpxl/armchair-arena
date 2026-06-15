"""Configuration loaded from .env (see project root)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _csv_lower(name: str, default: str = "") -> list[str]:
    """A comma-separated env var → list of stripped, lowercased, non-empty values."""
    return [v.strip().lower() for v in os.getenv(name, default).split(",") if v.strip()]


OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "").strip()
FIRECRAWL_URL = os.getenv("FIRECRAWL_URL", "http://localhost:3002").rstrip("/")

BIND_HOST = os.getenv("BIND_HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8090"))

# Hide models whose last update is older than this many days (0 = show all).
MAX_MODEL_AGE_DAYS = int(os.getenv("MAX_MODEL_AGE_DAYS", "365"))

# Hide models whose name contains any of these substrings (case-insensitive).
# Default drops coding/developer-tuned models, which aren't this tool's use case.
EXCLUDE_MODEL_PATTERNS = _csv_lower("EXCLUDE_MODEL_PATTERNS", "coder,code,devstral")

# Hide specific models by EXACT name (case-insensitive), e.g. sunset/retired models.
# Use this (not EXCLUDE_MODEL_PATTERNS) for exact versions — a substring like "minimax-m2"
# would also catch minimax-m2.1/2.5/2.7.
EXCLUDE_MODELS = set(_csv_lower("EXCLUDE_MODELS"))

# Free-text locale/region context injected into the system prompt (units, "near me",
# date format, spelling). Empty = omitted.
USER_LOCALE = os.getenv("USER_LOCALE", "").strip()

MAX_TOOL_ITERS = int(os.getenv("MAX_TOOL_ITERS", "5"))
SEARCH_SNIPPET_CHARS = int(os.getenv("SEARCH_SNIPPET_CHARS", "1500"))
SCRAPE_CHARS = int(os.getenv("SCRAPE_CHARS", "6000"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "300"))

DB_PATH = ROOT / "data" / "eval.db"
STATIC_DIR = ROOT / "static"
