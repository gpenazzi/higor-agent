"""Shared fixtures. Every test runs against a temporary ~/.higor and no network throttling."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from higor_agent.models import Paper, UserProfile


@pytest.fixture(autouse=True)
def isolated_higor_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect all on-disk state to a temp dir so tests never touch the real ~/.higor."""
    higor_dir = tmp_path / ".higor"
    monkeypatch.setattr("higor_agent.paths.HIGOR_DIR", higor_dir)
    monkeypatch.setattr("higor_agent.search.S2_MIN_INTERVAL", 0.0)
    monkeypatch.setattr("higor_agent.search._ARXIV_MIN_INTERVAL", 0.0)
    return higor_dir


def make_paper(idx: int, **kwargs) -> Paper:
    defaults = dict(
        id=f"s2:{idx:040x}",
        title=f"Paper {idx}: an interesting title about tight binding",
        authors=["Alice Smith", "Bob Jones", "Carol Lee", "Dan Wu"],
        journal="Physical Review B",
        abstract="We study things carefully and find interesting results.",
        url=f"https://www.semanticscholar.org/paper/{idx:040x}",
        published=datetime(2026, 9, 1 + idx % 10, tzinfo=timezone.utc),
    )
    defaults.update(kwargs)
    return Paper(**defaults)


def make_profile(**kwargs) -> UserProfile:
    defaults = dict(
        name="Ada",
        research_interests="Electronic structure methods.",
        primary_topics=["density functional theory", "tight binding"],
        secondary_topics=["GPU acceleration"],
        prominent_journals=["Physical Review B"],
        fields_of_study=["Physics"],
        arxiv_categories=["cond-mat.mtrl-sci"],
        own_paper_ids=["abc123"],
        own_paper_titles={"abc123": "My DFT paper"},
        max_papers_per_week=5,
        lookback_days=7,
    )
    defaults.update(kwargs)
    return UserProfile(**defaults)
