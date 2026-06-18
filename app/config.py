"""Configuration loaded from .env (see project root)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


# OLLAMA_HOST/OLLAMA_API_KEY seed the built-in "Ollama Cloud" backend. Additional backends
# (local/remote Ollama servers) are added at runtime via the model manager and persisted in
# the DB — see app/db.py get_config()/save_config().
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "").strip()
FIRECRAWL_URL = os.getenv("FIRECRAWL_URL", "http://localhost:3002").rstrip("/")

BIND_HOST = os.getenv("BIND_HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8090"))

# The ONLY discovery filter: hide models whose last update is older than this many days
# (0 = show all). Name/pattern exclusions were removed so every recent model is selectable
# in the manager — the user curates the roster instead.
MAX_MODEL_AGE_DAYS = int(os.getenv("MAX_MODEL_AGE_DAYS", "365"))

# Free-text locale/region context injected into the system prompt (units, "near me",
# date format, spelling). Empty = omitted.
USER_LOCALE = os.getenv("USER_LOCALE", "").strip()

# A model needs at least this many decided batches before its win-rate/strength is
# trusted; below it, analytics flags the model as low-data and keeps it off the
# efficiency frontier so a small-sample streak can't masquerade as the best model.
MIN_DECIDED = int(os.getenv("MIN_DECIDED", "5"))

MAX_TOOL_ITERS = int(os.getenv("MAX_TOOL_ITERS", "5"))
SEARCH_SNIPPET_CHARS = int(os.getenv("SEARCH_SNIPPET_CHARS", "1500"))
SCRAPE_CHARS = int(os.getenv("SCRAPE_CHARS", "6000"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "300"))

DB_PATH = ROOT / "data" / "eval.db"
STATIC_DIR = ROOT / "static"
