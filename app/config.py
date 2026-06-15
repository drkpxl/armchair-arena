"""Configuration loaded from .env (see project root)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "").strip()
FIRECRAWL_URL = os.getenv("FIRECRAWL_URL", "http://localhost:3002").rstrip("/")

BIND_HOST = os.getenv("BIND_HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8090"))

CLOUD_MODELS = [m.strip() for m in os.getenv("CLOUD_MODELS", "").split(",") if m.strip()]

# Hide models whose last update is older than this many days (0 = show all).
MAX_MODEL_AGE_DAYS = int(os.getenv("MAX_MODEL_AGE_DAYS", "365"))

# Hide models whose name contains any of these substrings (case-insensitive).
# Default drops coding/developer-tuned models, which aren't this tool's use case.
EXCLUDE_MODEL_PATTERNS = [
    p.strip().lower()
    for p in os.getenv("EXCLUDE_MODEL_PATTERNS", "coder,code,devstral").split(",")
    if p.strip()
]

MAX_TOOL_ITERS = int(os.getenv("MAX_TOOL_ITERS", "5"))
SEARCH_SNIPPET_CHARS = int(os.getenv("SEARCH_SNIPPET_CHARS", "1500"))
SCRAPE_CHARS = int(os.getenv("SCRAPE_CHARS", "6000"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "300"))

DB_PATH = ROOT / "data" / "eval.db"
STATIC_DIR = ROOT / "static"
