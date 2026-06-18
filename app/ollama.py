"""Native Ollama /api/chat client.

The native API (not the OpenAI-compatible /v1 surface) is used because it returns the
token counts and timing fields we evaluate on: prompt_eval_count, eval_count,
total_duration, eval_duration (durations in nanoseconds).
"""
from __future__ import annotations

from typing import Any

from datetime import datetime, timedelta, timezone

import httpx

from . import db
from .config import MAX_MODEL_AGE_DAYS, OLLAMA_API_KEY, OLLAMA_HOST


def _headers_for(api_key: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def resolve_backend(backend_id: str) -> tuple[str, dict[str, str]]:
    """(host, auth headers) for a backend id. The builtin cloud backend resolves to the
    env host/key (secret stays in .env); user-added backends use their stored host/key.
    Unknown ids fall back to cloud."""
    cfg = db.get_config()
    backend = next((b for b in cfg["backends"] if b.get("id") == backend_id), None)
    if backend is None or backend.get("builtin") or backend_id == db.CLOUD_BACKEND_ID:
        return OLLAMA_HOST, _headers_for(OLLAMA_API_KEY)
    return backend["host"].rstrip("/"), _headers_for(backend.get("api_key"))


def backend_for(model: str) -> tuple[str, dict[str, str]]:
    """(host, auth headers) for a model, routed via its roster entry's backend.
    Models not in the roster fall back to the cloud backend."""
    cfg = db.get_config()
    entry = next((r for r in cfg["roster"] if r.get("model") == model), None)
    return resolve_backend(entry["backend_id"] if entry else db.CLOUD_BACKEND_ID)


def backends_in_use() -> list[dict[str, Any]]:
    """Distinct backends referenced by the roster (cloud always included), for /api/health."""
    cfg = db.get_config()
    used = {r.get("backend_id", db.CLOUD_BACKEND_ID) for r in cfg["roster"]}
    used.add(db.CLOUD_BACKEND_ID)
    out: list[dict[str, Any]] = []
    for b in cfg["backends"]:
        if b["id"] in used:
            host, headers = resolve_backend(b["id"])
            out.append({"id": b["id"], "label": b.get("label", b["id"]),
                        "host": host, "headers": headers})
    return out


def _fresh_enough(modified_at: str | None, cutoff: datetime) -> bool:
    """True if the model is newer than the cutoff. Unknown/unparseable dates are kept."""
    if not modified_at:
        return True
    try:
        dt = datetime.fromisoformat(modified_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    return dt >= cutoff


def _human_count(n: object) -> str | None:
    """8000000000 -> '8B', 32768 -> '32K', 1048576 -> '1M'."""
    try:
        v = int(n)
    except (TypeError, ValueError):
        return str(n) if n else None
    for div, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if v >= div:
            q = v / div
            return (f"{q:.1f}".rstrip("0").rstrip(".")) + suffix
    return str(v)


async def show(client: httpx.AsyncClient, model: str) -> dict:
    """Compact metadata for one model from /api/show (for the hover tooltip)."""
    host, headers = backend_for(model)
    resp = await client.post(
        f"{host}/api/show", json={"model": model}, headers=headers
    )
    resp.raise_for_status()
    d = resp.json()
    details = d.get("details") or {}
    mi = d.get("model_info") or {}
    ctx = next((v for k, v in mi.items() if k.endswith("context_length")), None)
    return {
        "name": model,
        "architecture": mi.get("general.architecture") or details.get("family"),
        "parameter_size": _human_count(
            mi.get("general.parameter_count") or details.get("parameter_size")
        ),
        "context": _human_count(ctx),
        "capabilities": d.get("capabilities") or [],
        "quantization": details.get("quantization_level") or None,
        "modified_at": (d.get("modified_at") or "")[:10] or None,
    }


async def probe(
    client: httpx.AsyncClient, host: str, headers: dict[str, str]
) -> list[str]:
    """Model names a backend serves (/api/tags), filtered ONLY by MAX_MODEL_AGE_DAYS.
    Used by the model manager to discover what's available to add to the roster."""
    resp = await client.get(f"{host.rstrip('/')}/api/tags", headers=headers)
    resp.raise_for_status()
    models = resp.json().get("models", [])
    if MAX_MODEL_AGE_DAYS > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_MODEL_AGE_DAYS)
        models = [m for m in models if _fresh_enough(m.get("modified_at"), cutoff)]
    return sorted(m["name"] for m in models)


async def chat(
    client: httpx.AsyncClient,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    """One non-streaming /api/chat turn. Returns the raw response dict."""
    payload = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "stream": False,
    }
    host, headers = backend_for(model)
    resp = await client.post(f"{host}/api/chat", json=payload, headers=headers)
    resp.raise_for_status()
    return resp.json()
