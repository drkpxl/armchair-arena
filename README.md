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

Models are served through [Ollama](https://ollama.com) (cloud or a local daemon), and every
model is given the **same web-research tools** (powered by a self-hosted
[Firecrawl](https://github.com/firecrawl/firecrawl)) so the comparison is fair.

---

## Screenshots

**Compare** — ask once, see answers side-by-side with live metrics, the source links each model
pulled in, and a one-click **winner pick**:

![Compare page](docs/compare.png)

**Analytics** — an opponent-aware **strength (Elo) leaderboard** across *all* your tests, a
quality-vs-cost scatter, and a sortable run table (with CSV export):

![Analytics page](docs/analytics.png)

## Features

- **Side-by-side comparison** — ask once, run exactly 3 models concurrently (random-seeded or hand-picked).
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
  capabilities (tools/vision/thinking), and quantization. Stale models (not updated in N months)
  are filtered out.

## How it works

```
Browser ──> FastAPI app (this repo) ──> Ollama   /api/chat, /api/tags, /api/show
                  │                      (cloud at ollama.com, or a local daemon)
                  ├─────────────────────> Firecrawl  /v1/search, /v1/scrape  (web tools)
                  └─────────────────────> SQLite  (runs + sources)
```

When you ask a question, each selected model runs an independent tool-calling loop: it may call
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

Then open the bind address you set (default `http://127.0.0.1:8090`), and check
`http://127.0.0.1:8090/api/health` to confirm the backends are reachable.

### Setup via an AI agent (Hermes / OpenClaw)

This repo ships an [`AGENTS.md`](AGENTS.md) with an explicit, verifiable recipe. Point your
agent at it — e.g. *"clone https://github.com/<you>/armchair-arena and follow AGENTS.md; here's
my Ollama API key."* The agent installs it, fills in the key, starts the service, and confirms
success by polling `/api/health`.

## Configuration (`.env`)

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_HOST` | `https://ollama.com` | Ollama base URL. Use `http://localhost:11434` for a local daemon. |
| `OLLAMA_API_KEY` | — | API key for Ollama Cloud. Leave blank for a local daemon. |
| `FIRECRAWL_URL` | `http://localhost:3002` | Self-hosted Firecrawl base URL. |
| `BIND_HOST` | `127.0.0.1` | Bind address. `0.0.0.0` exposes it to your network. |
| `PORT` | `8090` | Port to serve on. |
| `MAX_MODEL_AGE_DAYS` | `365` | Hide models not updated within this many days (`0` = show all). |
| `EXCLUDE_MODEL_PATTERNS` | `coder,code,devstral` | Hide models whose name contains any of these substrings (drops coding/dev-tuned models). Empty = show all. |
| `EXCLUDE_MODELS` | — | Hide specific models by **exact** name (comma-separated, case-insensitive) — e.g. retired/sunset models. Use this for exact versions; a substring would over-match. |
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
| `GET` | `/api/health` | Backend status: Ollama + a Firecrawl search canary (`web_tools` = search actually works). |
| `GET` | `/api/models` | Available models (age- and pattern-filtered). |
| `GET` | `/api/model_info?name=` | Metadata for one model (tooltip). |
| `POST` | `/api/ask` | `{question, models[]}` (**exactly 3**) -> start a batch; returns `batch_id`. |
| `GET` | `/api/batch/{id}` | Poll a running batch for answers + metrics + sources. |
| `POST` | `/api/winner` | `{run_id, win}` -> mark that run the batch winner (`win:false` clears it). |
| `GET` | `/api/analytics` | Per-model strength (Elo), win-rate + 95% CI, and cost/speed aggregates. |
| `GET` | `/api/runs` | All runs (raw). |
| `GET` | `/api/export.csv` | Runs CSV. |
| `GET` | `/api/export_sources.csv` | Sources CSV. |

## Data

SQLite at `data/eval.db` (created on first run):
- **`runs`** — one row per (question, model): answer, `win` (1 for the picked winner of its batch),
  tokens, timings, tool trace, error.
- **`sources`** — one row per URL a model touched: url, domain, role (`search_result`/`scraped`).

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
