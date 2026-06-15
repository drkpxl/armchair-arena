"""SQLite persistence for evaluation runs.

One row per (question, model) execution. A side-by-side comparison shares a batch_id.
Synchronous sqlite3 calls are wrapped in asyncio.to_thread by callers so they don't
block the event loop while models run concurrently.
"""
from __future__ import annotations

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
  rating INTEGER,
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
  credible INTEGER,
  ts TEXT,
  FOREIGN KEY (run_id) REFERENCES runs(id)
);
"""

# Columns persisted from a run result dict, in order.
_RUN_COLS = [
    "ts", "batch_id", "question", "model", "answer", "prompt_tokens",
    "completion_tokens", "total_tokens", "total_duration_ms", "eval_duration_ms",
    "tokens_per_sec", "wall_clock_ms", "tool_calls", "tool_trace", "error",
]


@contextmanager
def _db():
    """A connection that commits on success and is always closed (no leak)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        with conn:  # commit / rollback
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _db() as conn:
        conn.executescript(SCHEMA)


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
                "INSERT INTO sources (run_id, url, domain, role, credible, ts) "
                "VALUES (?, ?, ?, ?, NULL, ?)",
                (run_id, url, domain, s.get("role"), ts),
            )
            out.append({
                "id": int(cur.lastrowid), "url": url, "domain": domain,
                "role": s.get("role"), "credible": None,
            })
    return out


def set_source_credible(source_id: int, credible: bool) -> bool:
    with _db() as conn:
        cur = conn.execute(
            "UPDATE sources SET credible = ? WHERE id = ?",
            (1 if credible else 0, source_id),
        )
        return cur.rowcount > 0


def all_sources() -> list[dict[str, Any]]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT s.*, r.model, r.question FROM sources s "
            "JOIN runs r ON r.id = s.run_id ORDER BY s.id DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def set_rating(run_id: int, rating: int) -> bool:
    with _db() as conn:
        cur = conn.execute(
            "UPDATE runs SET rating = ? WHERE id = ?", (rating, run_id)
        )
        return cur.rowcount > 0


def analytics() -> list[dict[str, Any]]:
    """Per-model aggregates across all runs."""
    sql = """
      SELECT
        model,
        COUNT(*)                                         AS n,
        COUNT(rating)                                    AS n_rated,
        ROUND(AVG(rating), 2)                            AS avg_rating,
        ROUND(AVG(total_tokens), 0)                      AS avg_total_tokens,
        ROUND(AVG(completion_tokens), 0)                 AS avg_completion_tokens,
        ROUND(AVG(tokens_per_sec), 1)                    AS avg_tokens_per_sec,
        ROUND(AVG(wall_clock_ms), 0)                     AS avg_wall_clock_ms,
        ROUND(AVG(tool_calls), 2)                        AS avg_tool_calls,
        SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) AS errors,
        (SELECT COUNT(s.credible) FROM sources s JOIN runs r2 ON r2.id = s.run_id
          WHERE r2.model = runs.model)                   AS source_votes,
        (SELECT ROUND(AVG(s.credible) * 100, 0) FROM sources s JOIN runs r2 ON r2.id = s.run_id
          WHERE r2.model = runs.model)                   AS source_credible_pct
      FROM runs
      GROUP BY runs.model
      ORDER BY avg_rating DESC NULLS LAST, avg_total_tokens ASC
    """
    with _db() as conn:
        return [dict(r) for r in conn.execute(sql).fetchall()]


def all_runs() -> list[dict[str, Any]]:
    with _db() as conn:
        rows = conn.execute("SELECT * FROM runs ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]
