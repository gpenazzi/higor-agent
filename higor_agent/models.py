"""Shared Pydantic models — the single contract across every module and the MCP wire.

Deliberately free of any LLM concept: nothing here knows which harness is driving.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


class HigorError(Exception):
    """Base exception for all higor-agent errors."""


class SearchError(HigorError):
    """Raised when a literature source fails."""


class ProfileError(HigorError):
    """Raised when the profile on disk is missing or unreadable."""


class UserProfile(BaseModel):
    name: str = ""
    semantic_scholar_author_id: Optional[str] = None
    research_interests: str = ""
    primary_topics: list[str] = Field(default_factory=list)
    secondary_topics: list[str] = Field(default_factory=list)
    prominent_journals: list[str] = Field(default_factory=list)
    fields_of_study: list[str] = Field(default_factory=list)   # S2 taxonomy, e.g. "Physics"
    arxiv_categories: list[str] = Field(default_factory=list)  # e.g. "cond-mat.mtrl-sci"
    own_paper_ids: list[str] = Field(default_factory=list)
    own_paper_titles: dict[str, str] = Field(default_factory=dict)  # id → title
    max_papers_per_week: int = 15
    lookback_days: int = 7
    impact_weight: float = 0.25  # how much venue prestige + citations shift the final score

    def topics(self) -> list[str]:
        return self.primary_topics + self.secondary_topics


class Paper(BaseModel):
    id: str                      # "s2:<paperId>" or "arxiv:<id>"
    title: str
    authors: list[str] = Field(default_factory=list)
    journal: str = ""
    abstract: str = ""
    url: str = ""
    doi: str = ""
    arxiv_id: str = ""
    published: Optional[datetime] = None
    citation_count: int = 0
    cites_author_paper: str = ""      # title of the user's paper this cites; "" if none
    handle: str = ""                  # session handle, e.g. "#12"; written into the snapshot
    summary: str = ""                 # written by the agent
    topical_relevance: float = 0.0    # the agent's topical-fit judgement, before the blend
    venue_prestige: float = 0.0       # 0–1 venue standing; 0 = unknown/unscored
    relevance_score: float = 0.0      # final blended score

    def year(self) -> str:
        return self.published.strftime("%Y") if self.published else ""


class PaperInteraction(BaseModel):
    """User interaction with a paper, recorded in the reading log."""

    paper_id: str
    paper_title: str = ""
    action: str  # "opened" | "read_full" | "saved"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
