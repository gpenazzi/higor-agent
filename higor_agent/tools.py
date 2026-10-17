"""The tools, as plain async functions over a :class:`Session`.

Nothing here parses arguments: ``cli.py`` wraps these as commands, and the tests call them
directly. Every function returns plain text meant to be read by a model, and raises
``ValueError`` on a call the agent got wrong.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from higor_agent import prestige, search
from higor_agent.models import Paper, SearchError
from higor_agent.profile import describe_profile, save_profile
from higor_agent.rank import is_preprint
from higor_agent.session import MAX_READ, Session, brief

logger = logging.getLogger(__name__)

PAGE_SIZE = 200  # candidate rows per tool result


class Judgement(BaseModel):
    """One scored paper, as the agent reports it to ``show``."""

    ref: str = Field(description="Handle of the paper, e.g. '#12'")
    relevance: float = Field(description="Topical relevance from 0 to 1", ge=0.0, le=1.0)
    summary: str = Field(description="One or two specific sentences about the paper")


class Toolkit:
    """The tool surface over one session."""

    def __init__(self, session: Session | None = None) -> None:
        self.session = session if session is not None else Session()

    # ── Context ───────────────────────────────────────────────────────────────

    async def get_context(self) -> str:
        """Profile, reading history, and the list currently in the browser."""
        return self.session.context_block()

    # ── Collecting ────────────────────────────────────────────────────────────

    async def collect_candidates(self, days: int | None = None, page: int = 1) -> str:
        s = self.session
        days = int(days or s.profile.lookback_days)
        page = max(1, int(page or 1))
        if page == 1 or not s.candidates:
            s.candidates, note = await s.sweep(days)
        else:
            note = "cached sweep"
        total = len(s.candidates)
        start, end = (page - 1) * PAGE_SIZE, min(page * PAGE_SIZE, total)
        header = (
            f"{total} candidates from the last {days} days ({note}). Rows {start + 1}-{end}."
        )
        if end < total:
            header += f" Run candidates --page {page + 1} for the rest."
        return header + "\n" + s.table(s.candidates[start:end])

    async def search_papers(
        self, query: str, days: int | None = None, venues: list[str] | None = None
    ) -> str:
        s = self.session
        query = str(query or "").strip()
        if not query:
            raise ValueError("query is required")
        days = int(days or s.profile.lookback_days)
        venues = [v for v in (venues or []) if str(v).strip()]
        s2q = query if any(ch in query for ch in '"|+~*') else f'"{query}"'
        jobs = [
            search.s2_search(
                s2q, days=days, fields_of_study=s.profile.fields_of_study, venues=venues or None
            )
        ]
        if not venues:
            jobs.append(search.arxiv_search([query], s.profile.arxiv_categories, days=days))
        results = await asyncio.gather(*jobs, return_exceptions=True)
        lists = [r for r in results if not isinstance(r, BaseException)]
        failures = [str(r) for r in results if isinstance(r, BaseException)]
        papers = [s.register(p) for p in search.merge_papers(*lists)]
        papers.sort(key=lambda p: -(p.published.timestamp() if p.published else 0))
        header = f"{len(papers)} papers for '{query}' in the last {days} days."
        if failures:
            header += " Failures: " + "; ".join(brief(f, 80) for f in failures)
        return header + "\n" + s.table(papers[:PAGE_SIZE])

    async def related_papers(self, refs: list[str], days: int | None = None) -> str:
        s = self.session
        seeds = [p for p in (s.resolve(r) for r in refs or []) if p]
        if not seeds:
            raise ValueError("no known papers among refs")
        days = int(days) if days else None
        # arXiv-only seeds need their S2 record for the recommendation/citation APIs.
        pending = [p for p in seeds if not p.id.startswith("s2:")]
        if pending:
            try:
                for f in await search.s2_fetch([f"ARXIV:{p.arxiv_id}" for p in pending]):
                    seeds.append(s.register(f))
            except SearchError as exc:
                logger.warning("S2 lookup of arXiv seeds failed: %s", exc)
        s2_seeds = [p for p in seeds if p.id.startswith("s2:")]
        if not s2_seeds:
            return "None of these papers is indexed by Semantic Scholar yet."
        ids = [p.id.split(":", 1)[1] for p in s2_seeds]
        pairs = [(p.id.split(":", 1)[1], p.title) for p in s2_seeds]
        recs, cites = await asyncio.gather(
            search.s2_recommendations(ids, days=days),
            search.s2_citations_of(pairs, days=days),
            return_exceptions=True,
        )
        lists = [r for r in (recs, cites) if not isinstance(r, BaseException)]
        seed_ids = {p.id for p in seeds}
        # Citations here mean "cites the seed", not "cites the researcher": keep the flag off.
        papers = [
            s.register(p.model_copy(update={"cites_author_paper": ""}))
            for p in search.merge_papers(*lists)
            if p.id not in seed_ids
        ]
        papers.sort(key=lambda p: -(p.published.timestamp() if p.published else 0))
        return f"{len(papers)} related papers.\n" + s.table(papers[:PAGE_SIZE])

    async def get_papers(self, refs: list[str]) -> str:
        s = self.session
        wanted = [str(r) for r in (refs or [])][:MAX_READ]
        papers = [p for p in (s.resolve(r) for r in wanted) if p]
        unknown = [r for r in wanted if s.resolve(r) is None]
        await s.fill_details(papers)
        papers = [s.papers[p.id] for p in papers]
        text = "\n\n".join(s.paper_block(p) for p in papers) or "(no known papers)"
        if unknown:
            text += "\n\nUnknown refs: " + ", ".join(unknown)
        return text

    # ── Venue prestige ────────────────────────────────────────────────────────

    async def venues_needing_rating(self, refs: list[str] | None = None) -> str:
        """Journals among *refs* with no cached prestige rating. Empty means nothing to do."""
        s = self.session
        if refs:
            papers = [p for p in (s.resolve(r) for r in refs) if p]
        else:
            papers = list(s.candidates) or list(s.shown)
        venues = {p.journal for p in papers if p.journal and not is_preprint(p)}
        missing = prestige.unrated(venues)
        if not missing:
            return "No unrated venues. Every journal involved already has a cached rating."
        return (
            f"{len(missing)} venues have no prestige rating yet. Rate each from 0 to 1 for "
            "standing in this researcher's field and cache them with rate-venues:\n"
            + "\n".join(missing)
        )

    async def set_venue_ratings(self, ratings: dict[str, float]) -> str:
        """Cache prestige ratings so every later blend and future session reuses them."""
        stored = prestige.set_ratings(ratings or {})
        if not stored:
            return "No valid ratings given; nothing cached."
        lines = [f"{v}: {score:.2f}" for v, score in sorted(stored.items())]
        return f"Cached {len(stored)} venue ratings:\n" + "\n".join(lines)

    # ── Showing ───────────────────────────────────────────────────────────────

    async def show(
        self,
        papers: list[Judgement],
        note: str = "",
        mode: Literal["replace", "append"] = "replace",
    ) -> str:
        s = self.session
        judged: list[tuple[Paper, float, str]] = []
        unknown: list[str] = []
        for entry in papers or []:
            p = s.resolve(entry.ref)
            if p is None:
                unknown.append(entry.ref)
                continue
            judged.append((p, float(entry.relevance), entry.summary))
        if not judged:
            raise ValueError(
                "no known papers to show"
                + (f"; unknown refs: {', '.join(unknown)}" if unknown else "")
            )
        shown, missing_venues = await s.apply_show(judged, note=note, append=(mode == "append"))
        lines = [f"{s.handle(p)} {p.relevance_score:.2f} {p.title[:80]}" for p in shown]
        text = f"Browser now shows {len(shown)} papers (final scores):\n" + "\n".join(lines)
        if unknown:
            text += "\nUnknown refs skipped: " + ", ".join(unknown)
        if missing_venues:
            text += (
                f"\nNote: {len(missing_venues)} venues were treated as neutral because they have "
                "no cached rating: " + ", ".join(missing_venues[:10])
                + ". Rate them with rate-venues and run show again to refine the ranking."
            )
        return text

    # ── Profile ───────────────────────────────────────────────────────────────

    async def update_profile(
        self,
        research_interests: str | None = None,
        add_primary_topics: list[str] | None = None,
        remove_primary_topics: list[str] | None = None,
        add_secondary_topics: list[str] | None = None,
        remove_secondary_topics: list[str] | None = None,
        add_journals: list[str] | None = None,
        remove_journals: list[str] | None = None,
        add_arxiv_categories: list[str] | None = None,
        remove_arxiv_categories: list[str] | None = None,
        set_fields_of_study: list[str] | None = None,
        set_impact_weight: float | None = None,
        set_lookback_days: int | None = None,
        set_max_papers: int | None = None,
    ) -> str:
        s = self.session
        pr = s.profile
        update: dict[str, Any] = {}

        def edit(field: str, additions: list[str] | None, removals: list[str] | None) -> None:
            if additions is None and removals is None:
                return
            values = list(getattr(pr, field))
            for v in additions or []:
                if v not in values:
                    values.append(v)
            dropped = {v.lower() for v in (removals or [])}
            update[field] = [v for v in values if v.lower() not in dropped]

        edit("primary_topics", add_primary_topics, remove_primary_topics)
        edit("secondary_topics", add_secondary_topics, remove_secondary_topics)
        edit("prominent_journals", add_journals, remove_journals)
        edit("arxiv_categories", add_arxiv_categories, remove_arxiv_categories)
        if research_interests:
            update["research_interests"] = str(research_interests)
        if set_fields_of_study is not None:
            update["fields_of_study"] = [str(f) for f in set_fields_of_study]
        if set_impact_weight is not None:
            update["impact_weight"] = max(0.0, float(set_impact_weight))
        if set_lookback_days:
            update["lookback_days"] = max(1, int(set_lookback_days))
        if set_max_papers:
            update["max_papers_per_week"] = max(1, int(set_max_papers))
        if not update:
            return "Nothing to change.\n" + describe_profile(pr)
        s.profile = pr.model_copy(update=update)
        save_profile(s.profile)
        return "Profile saved:\n" + describe_profile(s.profile)
