"""Session state: the paper cache, the handle table, and the profile-driven sweep.

This is the half of the original ``higor/agent.py`` that is not an LLM session. It holds
no model, no client and no prompt: the host harness owns the conversation, this owns the
papers. Every command is its own process, so ``workspace.py`` saves the session between
calls with ``to_cache`` and restores it with ``from_cache``.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from datetime import datetime, timezone

from higor_agent import search, store
from higor_agent.models import Paper, SearchError, UserProfile
from higor_agent.prestige import prestige_for, unrated
from higor_agent.profile import describe_profile, load_profile, save_profile
from higor_agent.rank import is_preprint, score_papers

logger = logging.getLogger(__name__)

MAX_READ = 40  # abstracts per get_papers call


def brief(value: object, limit: int = 70) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


class Session:
    """Owns the paper cache, the handle table and the last sweep."""

    def __init__(self, profile: UserProfile | None = None, shown: list[Paper] | None = None) -> None:
        self.profile = profile if profile is not None else load_profile()
        self.papers: dict[str, Paper] = {}
        self.shown: list[Paper] = []
        self._handles: dict[str, str] = {}   # "#12" → paper id
        self._ids: dict[str, str] = {}       # paper id → "#12"
        self.candidates: list[Paper] = []
        if shown is None:
            restored = store.load_last_session()
            shown = restored[0] if restored else []
        self.shown = [self.register(p) for p in shown]

    # ── Persistence between commands ──────────────────────────────────────────

    def to_cache(self) -> dict:
        return {
            "papers": [p.model_dump(mode="json") for p in self.papers.values()],
            "candidates": [p.id for p in self.candidates],
            "shown": [p.id for p in self.shown],
        }

    @classmethod
    def from_cache(cls, data: dict, profile: UserProfile | None = None) -> Session:
        """Rebuild a session saved by ``to_cache``, handles included."""
        s = cls(profile=profile, shown=[])
        for raw in data.get("papers", []):
            p = Paper.model_validate(raw)
            if not p.handle:
                p = s.register(p)
                continue
            s.papers[p.id] = p
            s._handles[p.handle] = p.id
            s._ids[p.id] = p.handle
        s.candidates = [s.papers[i] for i in data.get("candidates", []) if i in s.papers]
        s.shown = [s.papers[i] for i in data.get("shown", []) if i in s.papers]
        return s

    # ── Paper cache and handles ───────────────────────────────────────────────

    def register(self, paper: Paper) -> Paper:
        """Store *paper*, merging into any existing record, and assign it a handle.

        A paper keeps the handle it was first given, so handles stay stable for the
        whole host conversation even as records are enriched.
        """
        old = self.papers.get(paper.id)
        if old is not None:
            paper = old.model_copy(update={
                k: v for k, v in paper.model_dump().items()
                if v not in ("", 0, 0.0, None, []) or k == "id"
            })
        if paper.id not in self._ids:
            handle = f"#{self._next_handle()}"
            self._handles[handle] = paper.id
            self._ids[paper.id] = handle
        paper = paper.model_copy(update={"handle": self._ids[paper.id]})
        self.papers[paper.id] = paper
        return paper

    def _next_handle(self) -> int:
        return max((int(h[1:]) for h in self._handles), default=0) + 1

    def handle(self, paper: Paper) -> str:
        return self._ids.get(paper.id, "")

    def resolve(self, ref: str) -> Paper | None:
        """Look up a paper by handle ('#12' or '12') or by raw id."""
        ref = str(ref).strip()
        if not ref:
            return None
        if ref.startswith("#") and ref in self._handles:
            return self.papers[self._handles[ref]]
        if ref.isdigit() and f"#{ref}" in self._handles:
            return self.papers[self._handles[f"#{ref}"]]
        if ref in self.papers:
            return self.papers[ref]
        for prefix in ("s2:", "arxiv:"):
            if prefix + ref in self.papers:
                return self.papers[prefix + ref]
        return None

    # ── Rendering for the model ───────────────────────────────────────────────

    def table(self, papers: list[Paper]) -> str:
        rows = []
        for p in papers:
            date = p.published.strftime("%Y-%m-%d") if p.published else "????-??-??"
            venue = (p.journal or "-")[:28]
            flags = "C" if p.cites_author_paper else " "
            extra = f" ({p.citation_count} cit.)" if p.citation_count else ""
            rows.append(f"{self.handle(p):>5} {date} {venue:<28} {flags} {p.title[:120]}{extra}")
        return "\n".join(rows) if rows else "(none)"

    def paper_block(self, p: Paper) -> str:
        meta = [", ".join(p.authors[:6]) + (" et al." if len(p.authors) > 6 else "")]
        if p.journal:
            meta.append(p.journal)
        if p.published:
            meta.append(p.published.strftime("%Y-%m-%d"))
        if p.citation_count:
            meta.append(f"{p.citation_count} citations")
        if p.cites_author_paper:
            meta.append(f'cites your "{p.cites_author_paper}"')
        abstract = p.abstract[:1500] if p.abstract else "(no abstract available)"
        return f"{self.handle(p)} {p.title}\n   {' · '.join(meta)}\n   {p.url}\n   {abstract}"

    def shown_block(self) -> str:
        if not self.shown:
            return "(nothing shown yet)"
        return "\n".join(
            f"{self.handle(p):>5} {p.relevance_score:.2f} {p.title[:100]}" for p in self.shown
        )

    def history_block(self, n: int = 60) -> str:
        by_action: dict[str, list[str]] = {}
        for ix in store.recent_interactions(n):
            titles = by_action.setdefault(ix.action, [])
            if len(titles) < 8 and ix.paper_title not in titles:
                titles.append(ix.paper_title or ix.paper_id)
        return "\n".join(f"{a}: " + "; ".join(t) for a, t in by_action.items()) or "(none)"

    # ── Sweeps ────────────────────────────────────────────────────────────────

    async def sweep(self, days: int) -> tuple[list[Paper], str]:
        """Run every profile-driven source concurrently; return merged candidates + a note."""
        pr = self.profile
        phrases = pr.topics()
        jobs: dict[str, Awaitable[list[Paper]]] = {}
        if phrases:
            jobs["topics"] = search.s2_search(
                search.phrase_query(phrases), days=days, fields_of_study=pr.fields_of_study
            )
            jobs["arXiv"] = search.arxiv_search(phrases, pr.arxiv_categories, days=days)
        if pr.prominent_journals:
            jobs["journals"] = search.s2_search(
                "", days=days, venues=pr.prominent_journals, limit=500
            )
        if pr.own_paper_ids:
            await self.ensure_own_titles()
            pr = self.profile
            pairs = [(pid, pr.own_paper_titles.get(pid) or pid) for pid in pr.own_paper_ids]
            jobs["citations"] = search.s2_citations_of(pairs, days=days)
            jobs["recommendations"] = search.s2_recommendations(pr.own_paper_ids, days=days)
        if not jobs:
            return [], "no sources configured in the profile"
        results = await asyncio.gather(*jobs.values(), return_exceptions=True)
        lists: list[list[Paper]] = []
        notes: list[str] = []
        for name, res in zip(jobs, results):
            if isinstance(res, BaseException):
                logger.warning("%s sweep failed: %s", name, res)
                notes.append(f"{name}: failed ({brief(res, 60)})")
            else:
                lists.append(res)
                notes.append(f"{name}: {len(res)}")
        merged = [self.register(p) for p in search.merge_papers(*lists)]
        merged.sort(key=lambda p: (
            not p.cites_author_paper,
            -(p.published.timestamp() if p.published else 0),
        ))
        return merged, ", ".join(notes)

    async def ensure_own_titles(self) -> None:
        """Back-fill titles for the user's own paper ids and persist them, once."""
        pr = self.profile
        missing = [pid for pid in pr.own_paper_ids if not pr.own_paper_titles.get(pid)]
        if not missing:
            return
        try:
            fetched = await search.s2_fetch(missing)
        except SearchError as exc:
            logger.warning("Could not fetch titles of own papers: %s", exc)
            return
        titles = dict(pr.own_paper_titles)
        titles.update({p.id.removeprefix("s2:"): p.title for p in fetched})
        if titles != pr.own_paper_titles:
            self.profile = pr.model_copy(update={"own_paper_titles": titles})
            save_profile(self.profile)

    async def fill_details(self, papers: list[Paper]) -> None:
        """Back-fill missing abstracts (S2 batch, TL;DR fallback) and missing authors or
        abstracts (Crossref by DOI). S2 often lacks both for very recent journal papers."""
        missing = [p for p in papers if not p.abstract]
        if missing:
            ids = [
                p.id.split(":", 1)[1] if p.id.startswith("s2:") else f"ARXIV:{p.arxiv_id}"
                for p in missing
            ]
            try:
                fetched = await search.s2_fetch(ids)
            except SearchError as exc:
                logger.warning("Abstract back-fill failed: %s", exc)
                fetched = []
            by_arxiv = {f.arxiv_id: f for f in fetched if f.arxiv_id}
            by_id = {f.id: f for f in fetched}
            for p in missing:
                f = by_id.get(p.id) or by_arxiv.get(p.arxiv_id)
                if f and f.abstract:
                    self.papers[p.id] = self.papers[p.id].model_copy(
                        update={"abstract": f.abstract}
                    )
        current = [self.papers[p.id] for p in papers if p.id in self.papers]
        for p in await search.crossref_fill(current):
            self.papers[p.id] = p

    # ── Showing ───────────────────────────────────────────────────────────────

    async def apply_show(
        self,
        judged: list[tuple[Paper, float, str]],
        *,
        note: str = "",
        append: bool = False,
    ) -> tuple[list[Paper], list[str]]:
        """Blend scores, persist the snapshot, and return ``(shown, unrated_venues)``."""
        await self.fill_details([p for p, _, _ in judged])
        judged = [(self.papers[p.id], rel, summ) for p, rel, summ in judged]
        venues = {p.journal for p, _, _ in judged if p.journal and not is_preprint(p)}
        scored = score_papers(judged, prestige_for(venues), self.profile.impact_weight)
        if append:
            new_ids = {p.id for p in scored}
            scored = sorted(
                [p for p in self.shown if p.id not in new_ids] + scored,
                key=lambda p: p.relevance_score,
                reverse=True,
            )
        self.shown = [self.register(p) for p in scored]
        store.save_last_session(self.shown, note)
        return self.shown, unrated(venues)

    def context_block(self) -> str:
        """Everything the host agent needs that the static instruction file cannot carry."""
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        return (
            f"TODAY\n{today}\n\n"
            f"RESEARCHER PROFILE\n{describe_profile(self.profile)}\n\n"
            f"READING HISTORY (most recent first)\n{self.history_block()}\n\n"
            f"PAPERS CURRENTLY SHOWN IN THE BROWSER\n{self.shown_block()}"
        )
