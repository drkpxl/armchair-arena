"""Opponent-aware model strength from 3-way "one winner" results.

The compare page pits exactly three models against each other and the user picks the
single best answer. That is genuine comparative data, so raw win-rate undersells it: a
model that beats *strong* opponents deserves more credit than one that beats weak ones.

We fit a Bradley-Terry model (each model i has a positive strength p_i, and
P(i beats j) = p_i / (p_i + p_j)) by maximum likelihood via the standard
minorization-maximization (MM) iteration. It's order-independent — correct for models
whose skill doesn't drift over time, unlike Elo's sequential update. Strengths are then
rescaled to a familiar ~1500 Elo-like number for display.

Win-rate gets a Wilson confidence interval and a low-sample flag so a 1-for-1 streak
reads as provisional, not as the best model.
"""
from __future__ import annotations

import math
from typing import Any


def pairwise_from_batches(rows: list[dict[str, Any]]) -> tuple[
    list[str], dict[tuple[str, str], int], dict[tuple[str, str], int]
]:
    """Turn decided_batches() rows into pairwise win/meeting counts.

    In each batch the winner (win == 1) beat every other participant once. Returns the
    sorted model list, wins[(a, b)] = times a beat b, and meetings[(a, b)] = times a and
    b met (symmetric).
    """
    by_batch: dict[str, dict[str, int]] = {}
    for r in rows:
        by_batch.setdefault(r["batch_id"], {})[r["model"]] = r["win"]

    models: set[str] = set()
    wins: dict[tuple[str, str], int] = {}
    meetings: dict[tuple[str, str], int] = {}
    for participants in by_batch.values():
        names = list(participants)
        models.update(names)
        winners = [m for m, w in participants.items() if w]
        if not winners:
            continue  # shouldn't happen (decided batches only), but be safe
        winner = winners[0]
        for loser in names:
            if loser == winner:
                continue
            wins[(winner, loser)] = wins.get((winner, loser), 0) + 1
            for a, b in ((winner, loser), (loser, winner)):
                meetings[(a, b)] = meetings.get((a, b), 0) + 1
    return sorted(models), wins, meetings


def bradley_terry(
    models: list[str],
    wins: dict[tuple[str, str], int],
    meetings: dict[tuple[str, str], int],
    anchor_games: float = 1.0,
    iters: int = 200,
    tol: float = 1e-9,
) -> dict[str, dict[str, float]]:
    """Maximum-likelihood Bradley-Terry strengths via the MM algorithm.

    `anchor_games` adds a half-win + half-loss against a fixed average-strength phantom
    opponent (p = 1). This keeps undefeated/winless models finite, pulls thin samples
    toward the mean, and ties an otherwise-disconnected comparison graph to a common
    reference so the fit always converges.
    """
    if not models:
        return {}

    # Total pairwise wins and per-opponent meeting counts for each model.
    total_wins = {m: 0.0 for m in models}
    opponents: dict[str, dict[str, int]] = {m: {} for m in models}
    for (a, b), c in wins.items():
        total_wins[a] += c
    for (a, b), c in meetings.items():
        opponents[a][b] = opponents[a].get(b, 0) + c

    p = {m: 1.0 for m in models}
    half = anchor_games / 2.0
    for _ in range(iters):
        new_p = {}
        for m in models:
            w = total_wins[m] + half  # regularization win vs anchor (p = 1)
            denom = anchor_games / (p[m] + 1.0)  # games vs anchor
            for opp, c in opponents[m].items():
                denom += c / (p[m] + p[opp])
            new_p[m] = w / denom if denom else p[m]
        # Normalize so the geometric mean of strengths is 1 (fixes the scale).
        log_mean = sum(math.log(v) for v in new_p.values()) / len(new_p)
        norm = math.exp(log_mean)
        new_p = {m: v / norm for m, v in new_p.items()}
        delta = max(abs(math.log(new_p[m]) - math.log(p[m])) for m in models)
        p = new_p
        if delta < tol:
            break

    return {
        m: {"strength": round(p[m], 4), "elo": round(1500 + 400 * math.log10(p[m]))}
        for m in models
    }


def wilson(wins: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    """95% Wilson score interval for a win-rate (no scipy dependency)."""
    if not n:
        return (None, None)
    phat = wins / n
    z2 = z * z
    denom = 1 + z2 / n
    center = (phat + z2 / (2 * n)) / denom
    half = (z / denom) * math.sqrt(phat * (1 - phat) / n + z2 / (4 * n * n))
    return (max(0.0, center - half), min(1.0, center + half))


def enrich(
    agg_rows: list[dict[str, Any]],
    batch_rows: list[dict[str, Any]],
    min_decided: int,
) -> list[dict[str, Any]]:
    """Merge Bradley-Terry strength + Wilson CI + low-data flag onto the SQL aggregates.

    Returns rows sorted by strength (Elo) descending, with NULL/low-data models last.
    """
    models, wins, meetings = pairwise_from_batches(batch_rows)
    strength = bradley_terry(models, wins, meetings)

    out: list[dict[str, Any]] = []
    for row in agg_rows:
        m = dict(row)
        s = strength.get(m["model"])
        m["elo"] = s["elo"] if s else None  # raw `strength` p-value isn't surfaced; elo is
        decided = m.get("decided") or 0
        won = m.get("wins") or 0
        lo, hi = wilson(won, decided)
        m["ci_low"] = lo
        m["ci_high"] = hi
        m["low_data"] = decided < min_decided
        out.append(m)

    # Strength desc; models without a strength (no decided batches) sink to the bottom.
    out.sort(key=lambda r: (r["elo"] is None, -(r["elo"] or 0)))
    return out
