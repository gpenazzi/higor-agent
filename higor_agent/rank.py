"""Deterministic scoring: blend the agent's topical judgement with venue impact."""
from __future__ import annotations

import math

from higor_agent.models import Paper

_PREPRINT_VENUES = (
    "arxiv", "biorxiv", "medrxiv", "chemrxiv", "ssrn",
    "research square", "techrxiv", "preprints.org", "osf",
)
NEUTRAL_PRESTIGE = 0.5


def is_preprint(paper: Paper) -> bool:
    """Preprints are prestige-neutral: never penalised for lacking a journal."""
    journal = paper.journal.strip().lower()
    if any(v in journal for v in _PREPRINT_VENUES):
        return True
    if not journal:
        return "arxiv.org" in paper.url.lower() or bool(paper.arxiv_id)
    return False


def citation_norm(count: int) -> float:
    """Map a raw citation count to 0–1 on a log scale (1000 cites ≈ 1.0)."""
    if count <= 0:
        return 0.0
    return min(1.0, math.log10(1 + count) / 3.0)


def blend_score(relevance: float, prestige: float, cites: float, impact_weight: float) -> float:
    """``relevance · (1 + w · (venue_term + citation_bonus))``, clamped to [0, 1].

    Venue prestige is symmetric around 0.5 (good venues boost, weak ones penalise);
    citations are a bonus only, so a days-old uncited paper is never penalised. The
    adjustment is multiplicative, so off-topic work is not rescued by its venue.
    """
    adjust = impact_weight * ((prestige - 0.5) * 2.0 + cites)
    return max(0.0, min(1.0, relevance * (1.0 + adjust)))


def score_papers(
    judged: list[tuple[Paper, float, str]],
    prestige_map: dict[str, float],
    impact_weight: float,
) -> list[Paper]:
    """Attach scores/summaries and sort. *judged* holds ``(paper, topical, summary)``."""
    out: list[Paper] = []
    for paper, topical, summary in judged:
        topical = max(0.0, min(1.0, float(topical)))
        if is_preprint(paper) or not paper.journal:
            prestige = NEUTRAL_PRESTIGE
        else:
            prestige = prestige_map.get(paper.journal.strip().lower(), NEUTRAL_PRESTIGE)
        final = blend_score(topical, prestige, citation_norm(paper.citation_count), impact_weight)
        out.append(paper.model_copy(update={
            "topical_relevance": round(topical, 2),
            "venue_prestige": round(prestige, 2),
            "relevance_score": round(final, 2),
            "summary": summary.strip(),
        }))
    return sorted(out, key=lambda p: p.relevance_score, reverse=True)
