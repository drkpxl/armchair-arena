# AGENTS.md — setup recipe for AI coding agents

This file tells an AI agent (Hermes, OpenClaw, Claude Code, etc.) how to set up **Armchair
Arena** for a user. Follow it top to bottom; verify with the health check at the end.

## What you're setting up

A small FastAPI web app that lets the user compare LLMs side-by-side on non-coding tasks and
vote on quality. It talks to **Ollama** (cloud or a local daemon) for models and, optionally, a
self-hosted **Firecrawl** for web-research tools.

## Prerequisites (check, don't assume)

1. **Python 3.13+** and **uv** — `uv --version`. If missing:
   `curl -LsSf https://astral.sh/uv/install.sh | sh`
2. **A model backend**, one of:
   - **Ollama Cloud** — the user provides an API key from <https://ollama.com/settings/keys>.
   - **A local Ollama daemon** — reachable at `http://localhost:11434`
     (`curl -s localhost:11434/api/tags`).
3. **(Optional) Firecrawl** for web tools — <https://github.com/firecrawl/firecrawl>, expected
   at `http://localhost:3002`. **The app works without it** — models just answer from their own
   knowledge and the UI shows a notice. Do not block setup on Firecrawl.

## Steps

```bash
git clone <repo-url> armchair-arena && cd armchair-arena
cp .env.example .env
uv sync
```

Then edit `.env`:
- For **Ollama Cloud**: leave `OLLAMA_HOST=https://ollama.com` and set
  `OLLAMA_API_KEY=<the user's key>`. **Never commit `.env`** (it's gitignored) or print the key.
- For a **local daemon**: set `OLLAMA_HOST=http://localhost:11434` and leave `OLLAMA_API_KEY` blank.
- Optionally set `BIND_HOST` (use `127.0.0.1` for local only, or a private/VPN/tailnet hostname
  to reach it from another device — **the app has no auth, never bind it to the public internet**)
  and `PORT`.

Run it (foreground):

```bash
uv run python -m app
```

Or install as a persistent service — see [`systemd/armchair-arena.service`](systemd/armchair-arena.service):

```bash
cp systemd/armchair-arena.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now armchair-arena
loginctl enable-linger "$USER"
```

## Verify success

Poll the health endpoint (replace host/port with the configured ones):

```bash
curl -s http://127.0.0.1:8090/api/health
```

Success looks like:

```json
{"ollama":{"ok":true,"host":"...","models":34},
 "firecrawl":{"url":"...","reachable":true,"search_ok":true,"results":3,"note":null},
 "web_tools":true,"ok":true}
```

- `ollama.ok: true` → models load; setup is functional. Confirm `models > 0`.
- `web_tools: false` → Firecrawl's search isn't returning results (the `firecrawl.note` says why:
  unreachable, or reachable-but-empty). **This is OK** — tell the user web research is off and how
  to fix it; don't treat it as a failure.
- `ok: false` → the model backend is unreachable; see troubleshooting.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ollama.ok: false`, 401/403 | Bad/missing `OLLAMA_API_KEY` for Ollama Cloud. Re-check the key. |
| `ollama.ok: false`, connection refused | `OLLAMA_HOST` wrong, or local daemon not running (`ollama serve`). |
| `models: 0` | Cloud key has no access, or no models pulled locally (`ollama pull <model>`). |
| `web_tools: false`, note "unreachable" | Firecrawl not running at `FIRECRAWL_URL`. Optional — app still works. |
| `web_tools: false`, note "no results" | Firecrawl up but search blocked (default DuckDuckGo gets anti-bot-blocked). Configure `SEARXNG_ENDPOINT` or a search-API key in Firecrawl. |
| Port already in use | Change `PORT` in `.env`. |
| `uv: command not found` | Install uv (see Prerequisites). |

## Notes

- All config lives in `.env`; never hardcode secrets in code or commit `.env`.
- The app stores data in `data/eval.db` (SQLite, created on first run).
