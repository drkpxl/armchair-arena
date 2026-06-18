# 🛋️ Armchair Arena

**Judge LLMs from your armchair.** A tiny, self-hosted arena for evaluating LLMs on
**real-world, non-coding tasks** — web research, summarization, advice, recipes, general
knowledge — and judging them on the three things that actually matter day to day: **was it
fast, was it token-efficient, and was the answer good?**

Too many open models, no idea which one should be your "set and forget" pick? Stop guessing.
Pit three against each other on *your* questions and crown the best answer each round — over many
rounds that becomes a defensible ranking of what actually helps you.

Pick 3 models, ask one question, see the answers **side-by-side**, and crown the best one. Every
run records the question, answer, the winner, tokens used, and wall-clock time to SQLite. An
analytics page turns that into an **opponent-aware strength rating** across all your tests, plus
CSV export.

Models are served through [Ollama](https://ollama.com) — **Ollama Cloud and/or any number of
local or remote Ollama servers you add by URL** (e.g. a workstation or a box on your tailnet).
On first run an onboarding screen lets you **curate the roster** of models you want to test;
your picks (and the servers they live on) are saved **server-side, so they persist across
sessions and browsers**, and you can edit the roster anytime. Every model is given the **same
web-research tools** (powered by a self-hosted
[Firecrawl](https://github.com/firecrawl/firecrawl)) so the comparison is fair.

---

## Screenshots

**Model manager** — on first run (and anytime via **⚙ Models**), curate which models compete.
Add a local/remote Ollama server by URL and pick which of its models to include; the freshness
filter is the only thing hidden. Selections persist across sessions and browsers:

![Model manager](docs/models.png)

**Compare** — ask once, see answers side-by-side with live metrics, the source links each model
pulled in, and a one-click **winner pick**:

![Compare page](docs/compare.png)

**Analytics** — an opponent-aware **strength (Elo) leaderboard** across *all* your tests, a
quality-vs-cost scatter, and a sortable run table (with CSV export):

![Analytics page](docs/analytics.png)

## Features

- **Curated model roster, multi-backend** — a first-run onboarding screen (and an edit-anytime
  **⚙ Models** manager) lets you pick exactly which models the arena offers. Mix **Ollama Cloud**
  with **local/remote Ollama servers you add by URL** — each model is routed to its own backend
  automatically. The roster is stored server-side (SQLite), so it **persists across sessions and
  browsers**.
- **Side-by-side comparison** — ask once, run exactly 3 models concurrently (random-seeded or hand-picked from your roster).
- **Real metrics** — tokens (prompt/completion), tokens/sec, wall-clock, and tool-call count,
  pulled from Ollama's native `/api/chat` timing fields.
- **Consistent tool use** — identical `web_search` + `scrape_url` tools (Firecrawl) offered to
  every model with `tool_choice=auto`; the model decides when to use them. Degrades gracefully:
  if Firecrawl search is down, models answer from their own knowledge (and `/api/health` says so).
- **Fair, current prompt** — every model gets the same system prompt with today's date injected
  (so "this year"/"latest" resolve correctly), optional locale/units, and a nudge to cite the
  source URLs it used.
- **Markdown answers** rendered properly.
- **Winner pick** — mark the single best answer of the three (re-pickable; click again to clear).
  One decisive judgment per round instead of fuzzy 1–5 ratings, so the data is more quantifiable.
- **Source links** — see every URL a model pulled in (search results + scraped pages), shown for
  context (read-only).
- **Opponent-aware strength + best-tradeoff finder** — every 3-way result feeds a **Bradley-Terry
  strength rating** (Elo-scaled; beating *strong* models counts more than beating weak ones),
  shown alongside win-rate with a **95% confidence interval** and a **low-data flag** so a small
  sample can't masquerade as the best. A Pareto **efficiency frontier** flags the models no other
  beats on strength, token cost, *and* speed at once (objective, no weighting), gated on sample
  size. Plus single-metric leaderboards, a strength-vs-cost scatter (frontier highlighted), a
  sortable run table, and CSV exports (runs + sources), across *all* your tests.
- **Model picker with tooltips** — hover any model to see params, architecture, context window,
  capabilities (tools/vision/thinking), and quantization. The only discovery filter is age —
  models not updated within `MAX_MODEL_AGE_DAYS` are hidden; everything else (including
  coding/dev-tuned models) is selectable, and you curate the rest via the roster.

## How it works

```
Browser ──> FastAPI app (this repo) ──> Ollama backends  /api/chat, /api/tags, /api/show
                  │                      (Ollama Cloud + any local/remote servers you add;
                  │                       each model is routed to its own backend)
                  ├─────────────────────> Firecrawl  /v1/search, /v1/scrape  (web tools)
                  └─────────────────────> SQLite  (runs + sources + roster/backends config)
```

Your roster (which models, on which backends) is saved in SQLite and resolved per-model at
request time, so a single comparison can span several Ollama hosts. When you ask a question,
each selected model runs an independent tool-calling loop: it may call
`web_search`/`scrape_url`, the app executes them against Firecrawl and feeds the results back,
and the loop continues until the model gives a final answer (or the tool budget is hit, at which
point it's asked for a final answer with tools disabled).

## Requirements

- **Python 3.13+** and [**uv**](https://docs.astral.sh/uv/)
- **An Ollama backend**, either:
  - **Ollama Cloud** — an API key from <https://ollama.com/settings/keys> (no GPU needed), or
  - **A local Ollama daemon** — <https://ollama.com/download>
- **A self-hosted Firecrawl** for the web tools — <https://github.com/firecrawl/firecrawl>
  (runs in Docker; the app expects it at `http://localhost:3002`).
  > **Reliable search:** Firecrawl's default search scrapes DuckDuckGo, which anti-bot-blocks
  > after a few rapid queries (a side-by-side run is a burst) and returns empty — models then
  > answer from memory. Point Firecrawl at a [SearXNG](https://github.com/searxng/searxng)
  > instance (`SEARXNG_ENDPOINT`, with JSON format enabled) or a search-API key for dependable
  > results. Check `/api/health` — `web_tools: true` means a canary search actually returned hits.

## Quick start

```bash
git clone <your-fork-url> armchair-arena && cd armchair-arena

# configure
cp .env.example .env
$EDITOR .env            # set OLLAMA_API_KEY (cloud) or point OLLAMA_HOST at your local daemon

# install deps + run
uv sync
uv run python -m app
```

Or just run `./setup.sh` (checks `uv`, creates `.env`, installs deps), then edit `.env` and
`uv run python -m app`.

Then open the bind address you set (default `http://127.0.0.1:8090`). **On first load an
onboarding screen opens** — pick the models you want to test (and optionally add a local/remote
Ollama server by URL), then save. You can re-edit this roster anytime via **⚙ Models** in the
header. Check `http://127.0.0.1:8090/api/health` to confirm every backend your roster uses is
reachable.

### Setup via an AI agent (Hermes / OpenClaw)

This repo ships an [`AGENTS.md`](AGENTS.md) with an explicit, verifiable recipe. Point your
agent at it — e.g. *"clone https://github.com/<you>/armchair-arena and follow AGENTS.md; here's
my Ollama API key."* The agent installs it, fills in the key, starts the service, and confirms
success by polling `/api/health`.

## Configuration (`.env`)

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_HOST` | `https://ollama.com` | Base URL for the built-in **Ollama Cloud** backend. Use `http://localhost:11434` for a local daemon. Additional backends are added in-app (stored in the DB), not here. |
| `OLLAMA_API_KEY` | — | API key for the built-in Ollama Cloud backend. Leave blank for a local daemon. |
| `FIRECRAWL_URL` | `http://localhost:3002` | Self-hosted Firecrawl base URL. |
| `BIND_HOST` | `127.0.0.1` | Bind address. `0.0.0.0` exposes it to your network. |
| `PORT` | `8090` | Port to serve on. |
| `MAX_MODEL_AGE_DAYS` | `365` | The **only** model-discovery filter: hide models not updated within this many days (`0` = show all). Which models actually appear is then curated via the in-app roster. |
| `USER_LOCALE` | — | Optional region/units context added to every system prompt (e.g. "The user is in the US (Mountain Time); prefer °F, US spelling, MM/DD/YYYY."). Empty = omitted. |
| `MAX_TOOL_ITERS` | `5` | Max tool-call rounds before forcing a final answer. |
| `SEARCH_SNIPPET_CHARS` | `1500` | Truncation cap per search result. |
| `SCRAPE_CHARS` | `6000` | Truncation cap per scraped page. |
| `REQUEST_TIMEOUT` | `300` | Per-request timeout (seconds). |

> **No authentication.** This app has no login. Only expose it on a trusted/private network
> (e.g. localhost, a VPN, or a tailnet). Don't put it on the public internet.

## Deployment (systemd user service)

For a persistent install, see [`systemd/armchair-arena.service`](systemd/armchair-arena.service):

```bash
cp systemd/armchair-arena.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now armchair-arena
loginctl enable-linger "$USER"   # survive logout/reboot
```

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Backend status: each Ollama backend the roster uses + a Firecrawl search canary (`web_tools` = search actually works). |
| `GET` | `/api/models` | The saved roster — the models the picker offers. |
| `GET` | `/api/roster` | Full roster + backends config for the manager (`onboarded`, `has_key`; API keys never returned). |
| `POST` | `/api/roster` | `{backends[], roster[]}` -> persist the roster. Validates unique model names + known backends. |
| `POST` | `/api/probe` | `{backend_id}` or `{host, api_key}` -> list a backend's models (age-filtered) for the manager. |
| `GET` | `/api/model_info?name=` | Metadata for one model (tooltip; routed to the model's backend). |
| `POST` | `/api/ask` | `{question, models[]}` (**exactly 3, must be in the roster**) -> start a batch; returns `batch_id`. |
| `GET` | `/api/batch/{id}` | Poll a running batch for answers + metrics + sources. |
| `POST` | `/api/winner` | `{run_id, win}` -> mark that run the batch winner (`win:false` clears it). |
| `GET` | `/api/analytics` | Per-model strength (Elo), win-rate + 95% CI, and cost/speed aggregates. |
| `GET` | `/api/runs` | All runs (raw). |
| `GET` | `/api/export.csv` | Runs CSV. |
| `GET` | `/api/export_sources.csv` | Sources CSV. |

## Data

SQLite at `data/eval.db` (created on first run):
- **`runs`** — one row per (question, model): answer, `win` (1 for the picked winner of its batch),
  tokens, timings, tool trace, error. `model` is the plain model name (the analytics key), so
  adding the roster feature needed **no migration** and all historical analytics is preserved.
- **`sources`** — one row per URL a model touched: url, domain, role (`search_result`/`scraped`).
- **`settings`** — key→JSON store holding your roster + backends config (one row). This is what
  makes selections persist across sessions and browsers. Backend API keys for user-added servers
  live here; the built-in cloud backend's key stays in `.env`.

## Notes

- The app uses Ollama's **native** `/api/chat` (not the OpenAI-compatible surface) because it
  returns the token-count and timing fields the metrics are built on.
- Reasoning models (those with a `thinking` capability) are handled — if `content` is empty the
  app falls back to the `thinking` text.
- Some models may behave poorly through a given backend (e.g. returning empty content); that's a
  real data point, but if a model never produces output, it's likely a backend/model quirk rather
  than a bug in this app.

## License

[MIT](LICENSE)
