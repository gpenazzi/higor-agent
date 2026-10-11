from datetime import datetime, timezone

from higor_agent.models import Paper, PaperInteraction, UserProfile


def test_profile_topics_concatenates_primary_and_secondary():
    p = UserProfile(primary_topics=["a"], secondary_topics=["b", "c"])
    assert p.topics() == ["a", "b", "c"]


def test_paper_year_and_defaults():
    p = Paper(id="s2:x", title="T", published=datetime(2026, 5, 1, tzinfo=timezone.utc))
    assert p.year() == "2026"
    assert p.relevance_score == 0.0 and p.abstract == "" and p.authors == []
    assert Paper(id="s2:y", title="T").year() == ""


def test_interaction_timestamp_is_utc_aware():
    ix = PaperInteraction(paper_id="s2:x", action="saved")
    assert ix.timestamp.tzinfo is not None
