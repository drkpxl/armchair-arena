"""SQLite persistence for evaluation runs.

One row per (question, model) execution. A side-by-side comparison shares a batch_id.
Synchronous sqlite3 calls are wrapped in asyncio.to_thread by callers so they don't
block the event loop while models run concurrently.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlparse

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  batch_id TEXT NOT NULL,
  question TEXT NOT NULL,
  model TEXT NOT NULL,
  answer TEXT,
  win INTEGER NOT NULL DEFAULT 0,
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  total_tokens INTEGER,
  total_duration_ms REAL,
  eval_duration_ms REAL,
  tokens_per_sec REAL,
  wall_clock_ms REAL,
  tool_calls INTEGER,
  tool_trace TEXT,
  error TEXT
);
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL,
  url TEXT NOT NULL,
  domain TEXT,
  role TEXT,
  ts TEXT,
  FOREIGN KEY (run_id) REFERENCES runs(id)
);
CREATE INDEX IF NOT EXISTS idx_runs_batch ON runs(batch_id);
-- Server-side, single-user app config (model roster + backends), one JSON doc per key.
-- This is what makes the roster persist across sessions and browsers (not localStorage).
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

# The always-present cloud backend. Its host/key are NOT stored here — they resolve from
# config.OLLAMA_HOST/OLLAMA_API_KEY at runtime, so the secret stays in .env.
CLOUD_BACKEND_ID = "cloud"

# Columns persisted from a run result dict, in order.
_RUN_COLS = [
    "ts", "batch_id", "question", "model", "answer", "prompt_tokens",
    "completion_tokens", "total_tokens", "total_duration_ms", "eval_duration_ms",
    "tokens_per_sec", "wall_clock_ms", "tool_calls", "tool_trace", "error",
]


@contextmanager
def _db():
    """A connection that commits on success and is always closed (no leak)."""
    conn = sqlite3.connect(DB_PATH, timeout=30)  # wait, don't error, on a busy lock
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # concurrent readers + one writer
    try:
        with conn:  # commit / rollback
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _db() as conn:
        conn.executescript(SCHEMA)


# ---- app config (model roster + backends) ----
# Cached in-process so backend_for() isn't a DB read on every model turn; invalidated on save.
_config_cache: dict[str, Any] | None = None


def _default_config() -> dict[str, Any]:
    return {
        "backends": [{"id": CLOUD_BACKEND_ID, "label": "Ollama Cloud", "builtin": True}],
        "roster": [],
    }


def _ensure_builtin(cfg: dict[str, Any]) -> dict[str, Any]:
    """Guarantee the cloud backend is always present and marked builtin, whatever was saved."""
    backends = [b for b in cfg.get("backends", []) if b.get("id") != CLOUD_BACKEND_ID]
    cfg["backends"] = [
        {"id": CLOUD_BACKEND_ID, "label": "Ollama Cloud", "builtin": True}
    ] + backends
    cfg.setdefault("roster", [])
    return cfg


def get_config() -> dict[str, Any]:
    """The persisted roster/backends config (cloud backend always present)."""
    global _config_cache
    if _config_cache is None:
        with _db() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = 'config'"
            ).fetchone()
        if row:
            try:
                cfg = json.loads(row["value"])
            except (ValueError, TypeError):
                cfg = _default_config()
        else:
            cfg = _default_config()
        _config_cache = _ensure_builtin(cfg)
    return _config_cache


def save_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Persist the roster/backends config and refresh the cache. Returns the stored doc."""
    global _config_cache
    cfg = _ensure_builtin(cfg)
    with _db() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('config', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (json.dumps(cfg),),
        )
    _config_cache = cfg
    return cfg


def insert_run(result: dict[str, Any]) -> int:
    placeholders = ", ".join("?" for _ in _RUN_COLS)
    cols = ", ".join(_RUN_COLS)
    values = [result.get(c) for c in _RUN_COLS]
    with _db() as conn:
        cur = conn.execute(
            f"INSERT INTO runs ({cols}) VALUES ({placeholders})", values
        )
        return int(cur.lastrowid)


def insert_sources(run_id: int, ts: str, sources: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Persist the source URLs for a run; return rows with their new ids."""
    out: list[dict[str, Any]] = []
    with _db() as conn:
        for s in sources:
            url = s["url"]
            domain = urlparse(url).netloc or None
            cur = conn.execute(
                "INSERT INTO sources (run_id, url, domain, role, ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (run_id, url, domain, s.get("role"), ts),
            )
            out.append({
                "id": int(cur.lastrowid), "url": url, "domain": domain,
                "role": s.get("role"),
            })
    return out


def all_sources() -> list[dict[str, Any]]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT s.*, r.model, r.question FROM sources s "
            "JOIN runs r ON r.id = s.run_id ORDER BY s.id DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def set_winner(run_id: int, win: bool = True) -> bool:
    """Mark one run as the batch winner (or clear it).

    A batch has at most one winner, so we always zero the whole batch first — that
    makes a re-pick (different card) and a clear (same card, win=False) the same code
    path. Both statements commit atomically via the `with conn:` transaction.
    """
    with _db() as conn:
        row = conn.execute(
            "SELECT batch_id FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return False
        conn.execute("UPDATE runs SET win = 0 WHERE batch_id = ?", (row["batch_id"],))
        if win:
            conn.execute("UPDATE runs SET win = 1 WHERE id = ?", (run_id,))
        return True


def analytics() -> list[dict[str, Any]]:
    """Per-model aggregates across all runs (win-rate based).

    A "decided appearance" is a non-errored run whose batch has a winner picked
    (SUM(win) > 0). Errored runs are excluded from the denominator so win_rate is
    measured over the same set of runs that feeds the strength fit (decided_batches()
    also filters error IS NULL) — the two numbers in a table row stay consistent.
    win_rate = wins / decided_appearances; NULL when a model has no decided batches.
    Opponent-aware strength is layered on top in app/analytics.py from decided_batches().
    """
    sql = """
      WITH decided AS (
        SELECT batch_id FROM runs GROUP BY batch_id HAVING SUM(win) > 0
      )
      SELECT
        r.model,
        COUNT(*)                                                AS n,
        SUM(r.win)                                              AS wins,
        SUM(CASE WHEN d.batch_id IS NOT NULL AND r.error IS NULL THEN 1 ELSE 0 END) AS decided,
        ROUND(CAST(SUM(r.win) AS REAL)
              / NULLIF(SUM(CASE WHEN d.batch_id IS NOT NULL AND r.error IS NULL THEN 1 ELSE 0 END), 0), 3)
                                                                AS win_rate,
        ROUND(AVG(r.total_tokens), 0)                           AS avg_total_tokens,
        ROUND(AVG(r.completion_tokens), 0)                      AS avg_completion_tokens,
        ROUND(AVG(r.tokens_per_sec), 1)                         AS avg_tokens_per_sec,
        ROUND(AVG(r.wall_clock_ms), 0)                          AS avg_wall_clock_ms,
        ROUND(AVG(r.tool_calls), 2)                             AS avg_tool_calls,
        SUM(CASE WHEN r.error IS NOT NULL THEN 1 ELSE 0 END)    AS errors
      FROM runs r
      LEFT JOIN decided d ON d.batch_id = r.batch_id
      GROUP BY r.model
      ORDER BY win_rate DESC NULLS LAST, avg_total_tokens ASC
    """
    with _db() as conn:
        return [dict(r) for r in conn.execute(sql).fetchall()]


def decided_batches() -> list[dict[str, Any]]:
    """One row per (model, win) for every batch that has a winner picked.

    Errored runs are excluded so a model isn't counted as a loser in a batch it
    crashed in; such a batch simply yields fewer pairwise comparisons.
    """
    sql = """
      WITH decided AS (
        SELECT batch_id FROM runs GROUP BY batch_id HAVING SUM(win) > 0
      )
      SELECT r.batch_id, r.model, r.win
      FROM runs r
      JOIN decided d ON d.batch_id = r.batch_id
      WHERE r.error IS NULL
    """
    with _db() as conn:
        return [dict(r) for r in conn.execute(sql).fetchall()]


def all_runs() -> list[dict[str, Any]]:
    with _db() as conn:
        rows = conn.execute("SELECT * FROM runs ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]
