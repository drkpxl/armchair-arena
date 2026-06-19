"""Execute (question, model) runs with a tool-calling loop, and batches concurrently."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from . import db, firecrawl, ollama, tools
from .config import MAX_TOOL_ITERS, REQUEST_TIMEOUT, USER_LOCALE

# Light, neutral system prompt: gives every model the same framing and nudges it to
# use the web tools when it needs current/factual info, without biasing answer quality.
# The current date is injected (industry-standard practice) so relative references like
# "this year" resolve correctly — models have no built-in clock and otherwise default to
# their training cutoff.
def _system_prompt(enable_tools: bool) -> str:
    now = datetime.now()
    today = f"{now:%A, %B} {now.day}, {now.year}"  # portable (no %-d, which fails on Windows)
    parts = [
        "You are a helpful assistant being evaluated (research, "
        "summarization, advice, general knowledge).",
        f"Today's date is {today}. Interpret relative time references such as "
        '"this year", "current", "now", "upcoming", or "latest" relative to today\'s '
        "date — do not assume an earlier year from your training data.",
    ]
    if USER_LOCALE:
        parts.append(USER_LOCALE)
    if enable_tools:
        parts.append(
            "You have web tools (web_search, scrape_url). Use them when the question needs "
            "current, factual, or external information, and include the relevant current "
            "year/date in your search queries; otherwise answer directly. When your answer "
            "draws on web results, cite the specific source URLs you relied on at the end."
        )
    parts.append("Be accurate and concise.")
    return " ".join(parts)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ns_to_ms(ns: Any) -> float:
    return round((ns or 0) / 1e6, 1)


def _usage(resp: dict[str, Any]) -> tuple[int, int, int, int]:
    """(prompt_eval_count, eval_count, total_duration, eval_duration), each defaulting to 0."""
    return (
        resp.get("prompt_eval_count") or 0,
        resp.get("eval_count") or 0,
        resp.get("total_duration") or 0,
        resp.get("eval_duration") or 0,
    )


def _final_answer(msg: dict[str, Any]) -> str:
    """Final answer text; reasoning models may leave content empty and use `thinking`."""
    return (msg.get("content") or "").strip() or (msg.get("thinking") or "")


def _is_tools_unsupported(exc: httpx.HTTPStatusError) -> bool:
    """True when Ollama rejected the request because the model can't use tools.
    Native /api/chat returns HTTP 400 with a body like '<model> does not support tools'."""
    resp = exc.response
    return resp is not None and resp.status_code == 400 and "support tools" in resp.text.lower()


async def _chat(
    client: httpx.AsyncClient, model: str, messages: list[dict[str, Any]],
    tool_schemas: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """One chat turn that degrades gracefully when a model lacks tool support.

    Capability detection (ollama.supports_tools) catches most non-tool models up front,
    but a backend may not report capabilities, so this is the safety net: if a turn 400s
    with 'does not support tools', drop the tools and retry. Returns (response, schemas)
    with the schemas the caller should keep using for the rest of the loop.
    """
    try:
        return await ollama.chat(client, model, messages, tool_schemas), tool_schemas
    except httpx.HTTPStatusError as exc:
        if not tool_schemas or not _is_tools_unsupported(exc):
            raise
        return await ollama.chat(client, model, messages, []), []


async def run_model(
    question: str,
    model: str,
    batch_id: str,
    ollama_client: httpx.AsyncClient,
    fc_client: httpx.AsyncClient,
    enable_tools: bool = True,
) -> dict[str, Any]:
    """Run one model through the tool loop. Returns a persisted run dict (with id).

    When enable_tools is False (e.g. Firecrawl unreachable), the model answers directly
    from its own knowledge instead of being offered web tools. Tools are also withheld
    from models whose template can't use them, which otherwise 400 on /api/chat — those
    models simply answer directly rather than erroring out.
    """
    tool_schemas = tools.TOOLS if enable_tools else []
    if tool_schemas and await ollama.supports_tools(ollama_client, model) is False:
        tool_schemas = []
    # Keep the system prompt honest: only advertise web tools when this model is actually
    # offered them, so a non-tool model isn't told to call tools it doesn't have.
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _system_prompt(bool(tool_schemas))},
        {"role": "user", "content": question},
    ]
    prompt_tokens = completion_tokens = 0
    total_duration_ns = eval_duration_ns = 0
    tool_trace: list[dict[str, Any]] = []
    sources_map: dict[str, str] = {}  # url -> role (scraped wins over search_result)
    answer = ""
    error: str | None = None

    wall_start = time.perf_counter()
    try:
        for _ in range(MAX_TOOL_ITERS):
            resp, tool_schemas = await _chat(ollama_client, model, messages, tool_schemas)
            p, c, td, ed = _usage(resp)
            prompt_tokens += p
            completion_tokens += c
            total_duration_ns += td
            eval_duration_ns += ed

            msg = resp.get("message", {}) or {}
            messages.append(msg)
            calls = msg.get("tool_calls") or []
            if not calls:
                answer = _final_answer(msg)
                break

            for call in calls:
                fn = call.get("function", {}) or {}
                name = fn.get("name", "")
                args = fn.get("arguments", {}) or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                try:
                    result, srcs = await tools.dispatch(name, args, fc_client)
                except Exception as exc:  # tool failure shouldn't kill the run
                    result, srcs = f"Tool {name} failed: {exc}", []
                tool_trace.append(
                    {"tool": name, "args": args, "result_chars": len(result),
                     "results": len(srcs)}
                )
                for s in srcs:
                    url, role = s["url"], s["role"]
                    if role == "scraped" or url not in sources_map:
                        sources_map[url] = role
                messages.append(
                    {"role": "tool", "tool_name": name, "content": result}
                )
        else:
            # Tool budget exhausted (model kept calling tools). Force a final answer
            # with tools disabled so we always return something useful.
            messages.append({
                "role": "user",
                "content": (
                    "Stop searching and give your best final answer now using the "
                    "information already gathered. Do not call any tools."
                ),
            })
            resp = await ollama.chat(ollama_client, model, messages, [])
            p, c, td, ed = _usage(resp)
            prompt_tokens += p
            completion_tokens += c
            total_duration_ns += td
            eval_duration_ns += ed
            answer = _final_answer(resp.get("message", {}) or {})
            if not answer:
                error = f"Hit MAX_TOOL_ITERS ({MAX_TOOL_ITERS}); forced answer was empty."
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    # Explain an empty answer instead of showing a blank card with no reason. Keep it
    # accurate: report the actual token count and only cite hidden-channel models as a
    # likely (not definitive) cause when tokens were actually generated.
    if not answer and not error:
        if completion_tokens > 0:
            error = (
                f"{model} generated {completion_tokens} tokens but returned no displayable "
                "content (some reasoning models, e.g. gpt-oss on Ollama Cloud, expose only "
                "hidden channels)."
            )
        else:
            error = f"{model} returned an empty response (no content)."

    wall_ms = round((time.perf_counter() - wall_start) * 1000, 1)
    # Prefer eval_duration; fall back to total_duration when the host omits it.
    rate_secs = (eval_duration_ns or total_duration_ns) / 1e9
    tokens_per_sec = round(completion_tokens / rate_secs, 1) if rate_secs > 0 else None

    result = {
        "ts": _now_iso(),
        "batch_id": batch_id,
        "question": question,
        "model": model,
        "answer": answer,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "total_duration_ms": _ns_to_ms(total_duration_ns),
        "eval_duration_ms": _ns_to_ms(eval_duration_ns),
        "tokens_per_sec": tokens_per_sec,
        "wall_clock_ms": wall_ms,
        "tool_calls": len(tool_trace),
        "tool_trace": json.dumps(tool_trace),
        "error": error,
    }
    # Persist this run. A DB failure here must not sink the whole batch (asyncio.gather),
    # so degrade this one card to unsaved rather than raising.
    try:
        run_id = await asyncio.to_thread(db.insert_run, result)
        result["id"] = run_id
        src_list = [{"url": u, "role": r} for u, r in sources_map.items()]
        result["sources"] = await asyncio.to_thread(
            db.insert_sources, run_id, result["ts"], src_list
        )
    except Exception as exc:
        result["id"] = None
        result["sources"] = []
        result["error"] = f"{result.get('error') or ''} [not saved: {exc}]".strip()
    return result


# In-memory registry of in-flight / recently-finished batches. A batch runs as a
# background task decoupled from the HTTP request that started it, so the client can
# poll for results instead of holding one long request open. This is what makes the
# app survive mobile backgrounding: suspending the tab kills the open connection, but
# the batch keeps running server-side and each model's result is recoverable on the
# next poll (also already persisted to the DB by run_model).
_BATCHES: dict[str, dict[str, Any]] = {}
_BATCH_CAP = 50  # keep only the most recent N batches in memory


def _prune_batches() -> None:
    while len(_BATCHES) > _BATCH_CAP:
        _BATCHES.pop(next(iter(_BATCHES)))  # dicts preserve insertion order: drop oldest


async def _run_batch_bg(batch_id: str, question: str, models: list[str]) -> None:
    state = _BATCHES[batch_id]
    try:
        timeout = httpx.Timeout(REQUEST_TIMEOUT)
        async with httpx.AsyncClient(timeout=timeout) as ollama_client, \
                httpx.AsyncClient(timeout=timeout) as fc_client:
            web_ok = await firecrawl.available(fc_client)
            state["web_tools"] = web_ok

            async def one(m: str) -> None:
                try:
                    state["results"][m] = await run_model(
                        question, m, batch_id, ollama_client, fc_client, web_ok
                    )
                except Exception as exc:  # never let one model sink the batch
                    state["results"][m] = {
                        "model": m, "error": f"{type(exc).__name__}: {exc}",
                        "id": None, "sources": [], "tool_trace": "[]",
                    }

            await asyncio.gather(*(one(m) for m in models))
    except Exception as exc:
        state["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        state["done"] = True


def start_batch(question: str, models: list[str]) -> str:
    """Launch a batch in the background and return its id immediately."""
    batch_id = uuid.uuid4().hex
    _BATCHES[batch_id] = {
        "models": models, "results": {}, "web_tools": None,
        "error": None, "done": False, "task": None,
    }
    _prune_batches()
    # Hold a reference to the task so it isn't garbage-collected mid-run.
    _BATCHES[batch_id]["task"] = asyncio.create_task(
        _run_batch_bg(batch_id, question, models)
    )
    return batch_id


def batch_status(batch_id: str) -> dict[str, Any] | None:
    """Poll snapshot for a batch, or None if unknown (e.g. server restarted)."""
    state = _BATCHES.get(batch_id)
    if state is None:
        return None
    done_models = state["results"]
    return {
        "done": state["done"],
        "web_tools": state["web_tools"],
        "error": state["error"],
        "models": state["models"],
        "results": list(done_models.values()),
        "pending": [m for m in state["models"] if m not in done_models],
    }
