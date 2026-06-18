"""FastAPI app: compare page, analytics, and the JSON API."""
from __future__ import annotations

import asyncio
import csv
import io
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import analytics, db, firecrawl, ollama, runner
from .config import MIN_DECIDED, OLLAMA_API_KEY, STATIC_DIR


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Armchair Arena", lifespan=lifespan)


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    models: list[str] = Field(min_length=3, max_length=3)


class WinnerRequest(BaseModel):
    run_id: int
    win: bool = True


class Backend(BaseModel):
    id: str
    label: str
    host: str | None = None
    api_key: str | None = None
    builtin: bool = False


class RosterEntry(BaseModel):
    model: str
    backend_id: str


class RosterRequest(BaseModel):
    backends: list[Backend] = Field(default_factory=list)
    roster: list[RosterEntry] = Field(default_factory=list)


class ProbeRequest(BaseModel):
    backend_id: str | None = None
    host: str | None = None
    api_key: str | None = None


def _public_config() -> dict:
    """The roster/backends config for the client — api keys are never sent (only has_key)."""
    cfg = db.get_config()
    backends = []
    for b in cfg["backends"]:
        if b.get("builtin") or b["id"] == db.CLOUD_BACKEND_ID:
            host, _ = ollama.resolve_backend(b["id"])
            backends.append({"id": b["id"], "label": b.get("label", "Ollama Cloud"),
                             "host": host, "builtin": True, "has_key": bool(OLLAMA_API_KEY)})
        else:
            backends.append({"id": b["id"], "label": b.get("label", b["id"]),
                             "host": b.get("host"), "builtin": False,
                             "has_key": bool(b.get("api_key"))})
    return {"backends": backends, "roster": cfg["roster"], "onboarded": bool(cfg["roster"])}


@app.get("/api/health")
async def health() -> dict:
    """Setup verification: is each backend the roster uses reachable, plus Firecrawl? Web
    tools degrade gracefully. The roster can span several Ollama hosts, so ping each."""
    out: dict = {}
    backends: list[dict] = []
    async with httpx.AsyncClient(timeout=8) as client:
        for b in ollama.backends_in_use():
            entry = {"id": b["id"], "label": b["label"], "host": b["host"]}
            try:
                resp = await client.get(f"{b['host'].rstrip('/')}/api/tags", headers=b["headers"])
                resp.raise_for_status()
                entry["ok"] = True
            except Exception as exc:
                entry["ok"] = False
                entry["error"] = f"{type(exc).__name__}: {exc}"
            backends.append(entry)
        out["firecrawl"] = await firecrawl.search_health(client)
    out["backends"] = backends
    # Summarize into the legacy `ollama` field the frontend banner reads (static/app.js).
    failed = [b for b in backends if not b["ok"]]
    out["ollama"] = {
        "ok": not failed,
        "host": ", ".join(b["host"] for b in backends),
        "error": "; ".join(f"{b['label']}: {b['error']}" for b in failed) or None,
    }
    out["web_tools"] = out["firecrawl"]["search_ok"]
    out["ok"] = out["ollama"]["ok"]  # all backends the roster needs must respond
    return out


@app.get("/api/models")
async def get_models() -> dict:
    """The user's saved roster — the models the picker offers (keeps the {models,error} shape)."""
    cfg = await asyncio.to_thread(db.get_config)
    return {"models": [r["model"] for r in cfg["roster"]], "error": None}


@app.get("/api/roster")
async def get_roster() -> dict:
    """Full roster + backends config for the model manager (no api keys, only has_key)."""
    return await asyncio.to_thread(_public_config)


@app.post("/api/roster")
async def set_roster(req: RosterRequest) -> dict:
    """Persist the model roster + backends. Validates unique model names and known backends."""
    names = [r.model for r in req.roster]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise HTTPException(status_code=400, detail=f"Duplicate model names not allowed: {dupes}")
    backend_ids = {b.id for b in req.backends} | {db.CLOUD_BACKEND_ID}
    bad = sorted({r.model for r in req.roster if r.backend_id not in backend_ids})
    if bad:
        raise HTTPException(status_code=400, detail=f"Roster references an unknown backend for: {bad}")
    # Preserve stored api keys for existing backends when the client (which never receives
    # keys) re-saves without re-entering them.
    existing_keys = {b["id"]: b.get("api_key", "") for b in db.get_config()["backends"]}
    stored_backends = []
    for b in req.backends:
        if b.builtin or b.id == db.CLOUD_BACKEND_ID:
            continue  # cloud is re-added from .env by db._ensure_builtin
        if not b.host:
            raise HTTPException(status_code=400, detail=f"Backend {b.label!r} needs a host URL")
        stored_backends.append({
            "id": b.id, "label": b.label, "host": b.host.rstrip("/"),
            "api_key": b.api_key if b.api_key else existing_keys.get(b.id, ""),
        })
    cfg = {
        "backends": stored_backends,
        "roster": [{"model": r.model, "backend_id": r.backend_id} for r in req.roster],
    }
    await asyncio.to_thread(db.save_config, cfg)
    return _public_config()


@app.post("/api/probe")
async def probe(req: ProbeRequest) -> dict:
    """List the models a backend serves (age-filtered), for the manager's add/connect flow.
    Accepts a saved backend_id, or a raw host (+optional key) for a not-yet-saved server."""
    if req.backend_id:
        host, headers = ollama.resolve_backend(req.backend_id)
    elif req.host:
        host = req.host.rstrip("/")
        headers = {"Authorization": f"Bearer {req.api_key}"} if req.api_key else {}
    else:
        raise HTTPException(status_code=400, detail="host or backend_id required")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            models = await ollama.probe(client, host, headers)
        return {"models": models, "error": None, "host": host}
    except Exception as exc:
        return {"models": [], "error": f"{type(exc).__name__}: {exc}", "host": host}


@app.get("/api/model_info")
async def model_info(name: str) -> dict:
    """Metadata for one model (hover tooltip in the picker)."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            return await ollama.show(client, name)
    except Exception as exc:
        return {"name": name, "error": f"{type(exc).__name__}: {exc}"}


@app.post("/api/ask")
async def ask(req: AskRequest) -> dict:
    """Start a batch in the background; the client polls /api/batch/{id} for results.

    Decoupling the run from this request is what lets the app survive mobile
    backgrounding — suspending the tab no longer aborts an in-flight comparison.
    """
    roster = {r["model"] for r in db.get_config()["roster"]}
    bad = [m for m in req.models if m not in roster]
    if bad:
        raise HTTPException(
            status_code=400,
            detail=f"Models not in your roster: {bad}. Add them in Manage Models.",
        )
    batch_id = runner.start_batch(req.question, req.models)
    return {"batch_id": batch_id, "models": req.models}


@app.get("/api/batch/{batch_id}")
async def batch(batch_id: str) -> dict:
    state = runner.batch_status(batch_id)
    if state is None:
        raise HTTPException(status_code=404, detail="batch not found")
    return state


@app.post("/api/winner")
async def winner(req: WinnerRequest) -> dict:
    ok = await asyncio.to_thread(db.set_winner, req.run_id, req.win)
    if not ok:
        raise HTTPException(status_code=404, detail="run not found")
    return {"ok": True}


@app.get("/api/analytics")
async def get_analytics() -> dict:
    agg = await asyncio.to_thread(db.analytics)
    batches = await asyncio.to_thread(db.decided_batches)
    return {"models": analytics.enrich(agg, batches, MIN_DECIDED)}


@app.get("/api/runs")
def get_runs() -> dict:
    return {"runs": db.all_runs()}


@app.get("/api/export.csv")
def export_csv() -> Response:
    rows = db.all_runs()
    buf = io.StringIO()
    fieldnames = [
        "id", "ts", "batch_id", "model", "question", "answer", "win",
        "prompt_tokens", "completion_tokens", "total_tokens",
        "total_duration_ms", "eval_duration_ms", "tokens_per_sec",
        "wall_clock_ms", "tool_calls", "tool_trace", "error",
    ]
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=armchair-arena-runs.csv"},
    )


@app.get("/api/export_sources.csv")
def export_sources_csv() -> Response:
    rows = db.all_sources()
    buf = io.StringIO()
    fieldnames = ["id", "run_id", "ts", "model", "url", "domain", "role", "question"]
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=armchair-arena-sources.csv"},
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/analytics")
def analytics_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "analytics.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
