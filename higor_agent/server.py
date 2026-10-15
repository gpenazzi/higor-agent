"""The MCP server: the one place that knows about MCP.

Run over stdio, so any host that speaks MCP can drive it — Claude Code, Copilot
CLI, Codex, Cursor. The host owns the conversation and the model; this process owns
the papers, the handles and the on-disk state for as long as the host keeps it alive.
"""
from __future__ import annotations

import logging
from typing import Literal

from collections.abc import Coroutine
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from higor_agent.models import HigorError
from higor_agent.session import MAX_READ
from higor_agent.tools import Judgement, Toolkit

logger = logging.getLogger(__name__)

READ_ONLY = ToolAnnotations(readOnlyHint=True)
WRITES = ToolAnnotations(readOnlyHint=False, destructiveHint=False)

INSTRUCTIONS = """\
Literature screening for one researcher: sweep Semantic Scholar and arXiv, read abstracts,
score topical relevance, and publish a ranked list to the researcher's terminal browser.

Call get_context first in every conversation: it carries the researcher's profile, their
reading history and whatever is currently shown, none of which is in your instructions.

Judge topical relevance only. Venue prestige and citation counts are blended in by this
server. Unknown journals need a rating once: venues_needing_rating tells you which,
set_venue_ratings caches your judgement for every future session.
"""


async def _run(work: Coroutine[Any, Any, str]) -> str:
    """Await a tool body, turning expected failures into messages the model can read.

    ``ToolError`` reaches the host as ``is_error`` with the text intact; anything else
    would be logged as a crash and reported as a bare "Error executing tool".
    """
    try:
        return await work
    except (HigorError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


def build_server(toolkit: Toolkit | None = None) -> MCPServer:
    """Register every tool on a fresh server. One Toolkit backs the whole process."""
    kit = toolkit if toolkit is not None else Toolkit()
    server = MCPServer(
        name="higor",
        title="Higor literature assistant",
        version="0.1.0",
        instructions=INSTRUCTIONS,
    )

    @server.tool(
        name="get_context",
        description=(
            "The researcher's profile, their recent reading history, today's date, and the "
            "papers currently shown in their browser. Call this first: none of it is in your "
            "instructions, and the profile is what every sweep is driven by."
        ),
        annotations=READ_ONLY,
        structured_output=False,
    )
    async def get_context() -> str:
        return await _run(kit.get_context())

    @server.tool(
        name="collect_candidates",
        description=(
            "Sweep every source configured in the profile (topic phrases on Semantic Scholar "
            "and arXiv, followed journals, papers citing the researcher, recommendations) for "
            "the last N days. Returns one row per candidate: handle, date, venue, C if it "
            "cites the researcher, title. Rows come in pages; call again with page=2, 3… "
            "until exhausted."
        ),
        annotations=READ_ONLY,
        structured_output=False,
    )
    async def collect_candidates(days: int | None = None, page: int = 1) -> str:
        return await _run(kit.collect_candidates(days=days, page=page))

    @server.tool(
        name="search_papers",
        description=(
            "Keyword search on Semantic Scholar and arXiv for one topic phrase (exact-phrase "
            "match). Use for topics not covered by the profile or to dig deeper. Restrict to "
            "specific venues with `venues`. Returns candidate rows."
        ),
        annotations=READ_ONLY,
        structured_output=False,
    )
    async def search_papers(
        query: str, days: int | None = None, venues: list[str] | None = None
    ) -> str:
        return await _run(kit.search_papers(query, days=days, venues=venues))

    @server.tool(
        name="related_papers",
        description=(
            "Papers related to the given handles: recommendations similar to them plus papers "
            "citing them. Use to follow a lead ('more like #12'). No date limit unless days "
            "is set."
        ),
        annotations=READ_ONLY,
        structured_output=False,
    )
    async def related_papers(refs: list[str], days: int | None = None) -> str:
        return await _run(kit.related_papers(refs, days=days))

    @server.tool(
        name="get_papers",
        description=(
            f"Full records (authors, venue, URL, abstract) for up to {MAX_READ} handles. "
            "Read abstracts before scoring or summarising: titles alone are not enough."
        ),
        annotations=READ_ONLY,
        structured_output=False,
    )
    async def get_papers(refs: list[str]) -> str:
        return await _run(kit.get_papers(refs))

    @server.tool(
        name="venues_needing_rating",
        description=(
            "Journals with no cached prestige rating, among the given handles or the current "
            "candidate set. Call before show, rate what comes back, then set_venue_ratings."
        ),
        annotations=READ_ONLY,
        structured_output=False,
    )
    async def venues_needing_rating(refs: list[str] | None = None) -> str:
        return await _run(kit.venues_needing_rating(refs))

    @server.tool(
        name="set_venue_ratings",
        description=(
            "Cache venue prestige as {journal name: 0-1}, where 0.5 is an ordinary venue, "
            "above that a strong one for this researcher's field. Rated once, reused for "
            "every future session; the cache is hand-editable JSON."
        ),
        annotations=WRITES,
        structured_output=False,
    )
    async def set_venue_ratings(ratings: dict[str, float]) -> str:
        return await _run(kit.set_venue_ratings(ratings))

    @server.tool(
        name="show",
        description=(
            "Publish the ranked list to the researcher's browser. Each entry needs a handle, "
            "a topical relevance score from 0 to 1 and a one or two sentence summary. mode "
            "'replace' (default) shows only these papers; 'append' adds them to the current "
            "list. Venue prestige and citations are blended in automatically and the result "
            "is sorted by final score."
        ),
        annotations=WRITES,
        structured_output=False,
    )
    async def show(
        papers: list[Judgement],
        note: str = "",
        mode: Literal["replace", "append"] = "replace",
    ) -> str:
        return await _run(kit.show(papers, note=note, mode=mode))

    @server.tool(
        name="update_profile",
        description=(
            "Edit the saved research profile. Only the fields given are changed; list fields "
            "are added to or removed from. Persistent across sessions, so confirm with the "
            "researcher before calling unless they asked for the change."
        ),
        annotations=WRITES,
        structured_output=False,
    )
    async def update_profile(
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
        return await _run(kit.update_profile(
            research_interests=research_interests,
            add_primary_topics=add_primary_topics,
            remove_primary_topics=remove_primary_topics,
            add_secondary_topics=add_secondary_topics,
            remove_secondary_topics=remove_secondary_topics,
            add_journals=add_journals,
            remove_journals=remove_journals,
            add_arxiv_categories=add_arxiv_categories,
            remove_arxiv_categories=remove_arxiv_categories,
            set_fields_of_study=set_fields_of_study,
            set_impact_weight=set_impact_weight,
            set_lookback_days=set_lookback_days,
            set_max_papers=set_max_papers,
        ))

    return server


def serve() -> None:
    """Run the server over stdio. ``higor-agent serve`` and ``python -m`` both land here."""
    from higor_agent.cli import setup_logging

    setup_logging()
    build_server().run(transport="stdio")


if __name__ == "__main__":
    serve()
