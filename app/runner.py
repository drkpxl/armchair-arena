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
    from its own knowledge instead of being offered web tools.
    """
    tool_schemas = tools.TOOLS if enable_tools else []
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _system_prompt(enable_tools)},
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
            resp = await ollama.chat(ollama_client, model, messages, tool_schemas)
            prompt_tokens += resp.get("prompt_eval_count") or 0
            completion_tokens += resp.get("eval_count") or 0
            total_duration_ns += resp.get("total_duration") or 0
            eval_duration_ns += resp.get("eval_duration") or 0

            msg = resp.get("message", {}) or {}
            messages.append(msg)
            calls = msg.get("tool_calls") or []
            if not calls:
                # Reasoning models may leave content empty and put text in `thinking`.
                answer = (msg.get("content") or "").strip() or (msg.get("thinking") or "")
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
            prompt_tokens += resp.get("prompt_eval_count") or 0
            completion_tokens += resp.get("eval_count") or 0
            total_duration_ns += resp.get("total_duration") or 0
            eval_duration_ns += resp.get("eval_duration") or 0
            fmsg = resp.get("message", {}) or {}
            answer = (fmsg.get("content") or "").strip() or (fmsg.get("thinking") or "")
            if not answer:
                error = f"Hit MAX_TOOL_ITERS ({MAX_TOOL_ITERS}); forced answer was empty."
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    # Explain the silent-empty case (e.g. gpt-oss on Ollama Cloud emits only hidden
    # channels) instead of showing a blank card with no reason.
    if not answer and not error:
        error = (
            f"Model generated {completion_tokens} tokens but returned no displayable "
            "content — some Ollama Cloud reasoning models (e.g. gpt-oss) expose only "
            "hidden reasoning channels."
        )

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
    run_id = await asyncio.to_thread(db.insert_run, result)
    result["id"] = run_id
    src_list = [{"url": u, "role": r} for u, r in sources_map.items()]
    result["sources"] = await asyncio.to_thread(
        db.insert_sources, run_id, result["ts"], src_list
    )
    return result


async def run_batch(question: str, models: list[str]) -> dict[str, Any]:
    """Run the question across all selected models concurrently."""
    batch_id = uuid.uuid4().hex
    timeout = httpx.Timeout(REQUEST_TIMEOUT)
    async with httpx.AsyncClient(timeout=timeout) as ollama_client, \
            httpx.AsyncClient(timeout=timeout) as fc_client:
        web_ok = await firecrawl.available(fc_client)
        results = await asyncio.gather(
            *(
                run_model(question, m, batch_id, ollama_client, fc_client, web_ok)
                for m in models
            )
        )
    return {"batch_id": batch_id, "results": results, "web_tools": web_ok}
