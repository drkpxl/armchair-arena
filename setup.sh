#!/usr/bin/env bash
# One-shot setup. Safe to re-run. See README.md / AGENTS.md for details.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Checking for uv…"
if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found. Install it: https://docs.astral.sh/uv/getting-started/installation/"
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

echo "==> Creating .env (if missing)…"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "    Created .env from .env.example."
  echo "    >>> Edit .env and set OLLAMA_API_KEY (Ollama Cloud) or point OLLAMA_HOST at a local daemon."
else
  echo "    .env already exists — leaving it alone."
fi

echo "==> Installing dependencies…"
uv sync

echo
echo "Done. Start it with:"
echo "    uv run python -m app"
echo
echo "Then verify setup at:  http://<BIND_HOST>:<PORT>/api/health"
echo "(web research needs a self-hosted Firecrawl; without it, models answer from their own knowledge)"
