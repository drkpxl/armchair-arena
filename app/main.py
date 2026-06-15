"""FastAPI app: compare page, analytics, and the JSON API."""
from __future__ import annotations

import asyncio
import csv
import io

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, firecrawl, ollama, runner
from .config import FIRECRAWL_URL, OLLAMA_HOST, STATIC_DIR

app = FastAPI(title="Armchair Arena")


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    models: list[str] = Field(min_length=1)


class VoteRequest(BaseModel):
    run_id: int
    rating: int = Field(ge=1, le=5)


class SourceVoteRequest(BaseModel):
    source_id: int
    credible: bool


@app.get("/api/health")
async def health() -> dict:
    """Setup verification: are Ollama and Firecrawl reachable? Web tools degrade gracefully."""
    out: dict = {}
    async with httpx.AsyncClient(timeout=8) as client:
        try:
            models = await ollama.list_models(client)
            out["ollama"] = {"ok": True, "host": OLLAMA_HOST, "models": len(models)}
        except Exception as exc:
            out["ollama"] = {"ok": False, "host": OLLAMA_HOST, "error": f"{type(exc).__name__}: {exc}"}
        web_ok = await firecrawl.available(client)
        out["firecrawl"] = {"ok": web_ok, "url": FIRECRAWL_URL}
    out["web_tools"] = out["firecrawl"]["ok"]
    out["ok"] = out["ollama"]["ok"]  # app is usable as long as a model backend works
    return out


@app.get("/api/models")
async def get_models() -> dict:
    """All models available from the configured Ollama host (/api/tags)."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            models = await ollama.list_models(client)
        return {"models": models, "error": None}
    except Exception as exc:
        return {"models": [], "error": f"{type(exc).__name__}: {exc}"}


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
    return await runner.run_batch(req.question, req.models)


@app.post("/api/vote")
async def vote(req: VoteRequest) -> dict:
    ok = await asyncio.to_thread(db.set_rating, req.run_id, req.rating)
    if not ok:
        raise HTTPException(status_code=404, detail="run not found")
    return {"ok": True}


@app.post("/api/source_vote")
async def source_vote(req: SourceVoteRequest) -> dict:
    ok = await asyncio.to_thread(db.set_source_credible, req.source_id, req.credible)
    if not ok:
        raise HTTPException(status_code=404, detail="source not found")
    return {"ok": True}


@app.get("/api/analytics")
def get_analytics() -> dict:
    return {"models": db.analytics()}


@app.get("/api/runs")
def get_runs() -> dict:
    return {"runs": db.all_runs()}


@app.get("/api/export.csv")
def export_csv() -> Response:
    rows = db.all_runs()
    buf = io.StringIO()
    fieldnames = [
        "id", "ts", "batch_id", "model", "question", "answer", "rating",
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
    fieldnames = ["id", "run_id", "ts", "model", "url", "domain", "role", "credible", "question"]
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
