"""Venue prestige: a plain cache of 0–1 venue standings, hand-editable JSON.

This module deliberately contains no model call. In the original Higor the tool layer
asked Claude to rate unknown journals, which pinned the whole package to one provider.
Here the *host* agent does the rating: it calls ``venues_needing_rating``, judges the
venues it gets back, and posts them with ``set_venue_ratings``. The package only
caches, validates and blends. That is what makes the tools usable from any harness.
"""
from __future__ import annotations

import json
import logging

from higor_agent import paths
from higor_agent.rank import NEUTRAL_PRESTIGE

logger = logging.getLogger(__name__)


def key(venue: str) -> str:
    return venue.strip().lower()


def load_ranks() -> dict[str, float]:
    """The cache as ``{lowercased_venue: 0–1}``; empty if absent or unreadable."""
    path = paths.journal_ranks_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): float(v) for k, v in raw.items()}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Venue-prestige cache read error (%s); ignoring.", exc)
        return {}


def save_ranks(ranks: dict[str, float]) -> None:
    path = paths.journal_ranks_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ranks, indent=2, sort_keys=True), encoding="utf-8")


def unrated(venues: set[str] | list[str]) -> list[str]:
    """Venues with no cached rating, in stable order, original spelling preserved."""
    cache = load_ranks()
    seen: set[str] = set()
    out: list[str] = []
    for v in sorted({v.strip() for v in venues if v and v.strip()}):
        k = key(v)
        if k not in cache and k not in seen:
            seen.add(k)
            out.append(v)
    return out


def set_ratings(ratings: dict[str, float]) -> dict[str, float]:
    """Merge *ratings* into the cache, clamped to 0–1. Returns what was stored."""
    cache = load_ranks()
    stored: dict[str, float] = {}
    for venue, value in ratings.items():
        if not venue or not str(venue).strip():
            continue
        try:
            score = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            logger.warning("Ignoring non-numeric rating for %r: %r", venue, value)
            continue
        stored[key(venue)] = score
    if stored:
        cache.update(stored)
        save_ranks(cache)
    return stored


def prestige_for(venues: set[str] | list[str]) -> dict[str, float]:
    """``{lowercased_venue: prestige}`` for every venue asked about.

    Unrated venues come back neutral, so a missing rating never penalises a paper.
    """
    cache = load_ranks()
    return {key(v): cache.get(key(v), NEUTRAL_PRESTIGE) for v in venues if v and v.strip()}
