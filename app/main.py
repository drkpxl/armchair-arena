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
from .config import MIN_DECIDED, OLLAMA_HOST, STATIC_DIR


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
        out["firecrawl"] = await firecrawl.search_health(client)
    out["web_tools"] = out["firecrawl"]["search_ok"]
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
    """Start a batch in the background; the client polls /api/batch/{id} for results.

    Decoupling the run from this request is what lets the app survive mobile
    backgrounding — suspending the tab no longer aborts an in-flight comparison.
    """
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
