"""The tool surface, called directly: no MCP, no model."""
import pytest

from higor_agent import prestige, store
from higor_agent.models import Paper
from higor_agent.profile import load_profile
from higor_agent.session import Session
from higor_agent.tools import Judgement, Toolkit
from tests.conftest import make_paper, make_profile


@pytest.fixture
def kit(monkeypatch) -> Toolkit:
    async def no_fill(self, papers):
        return None

    monkeypatch.setattr(Session, "fill_details", no_fill)
    return Toolkit(Session(profile=make_profile(), shown=[]))


class TestContext:
    async def test_get_context_reports_the_profile(self, kit):
        out = await kit.get_context()
        assert "Ada" in out
        assert "tight binding" in out


class TestCollect:
    async def test_pages_through_a_large_sweep(self, kit, monkeypatch):
        many = [make_paper(i, id=f"s2:{i}") for i in range(250)]

        async def sweep(self, days):
            return [self.register(p) for p in many], "topics: 250"

        monkeypatch.setattr(Session, "sweep", sweep)
        first = await kit.collect_candidates(days=7)
        assert "Rows 1-200" in first
        assert "--page 2" in first
        second = await kit.collect_candidates(page=2)
        assert "Rows 201-250" in second
        assert "cached sweep" in second

    async def test_defaults_the_window_to_the_profile(self, kit, monkeypatch):
        seen = {}

        async def sweep(self, days):
            seen["days"] = days
            return [], "none"

        monkeypatch.setattr(Session, "sweep", sweep)
        await kit.collect_candidates()
        assert seen["days"] == 7


class TestSearch:
    async def test_blank_query_is_rejected(self, kit):
        with pytest.raises(ValueError, match="query is required"):
            await kit.search_papers("   ")

    async def test_plain_phrases_are_quoted_and_venues_skip_arxiv(self, kit, monkeypatch):
        calls = {}

        async def s2(query, **kw):
            calls["query"] = query
            calls["venues"] = kw.get("venues")
            return [make_paper(1)]

        async def arxiv(*a, **k):
            calls["arxiv"] = True
            return []

        monkeypatch.setattr("higor_agent.tools.search.s2_search", s2)
        monkeypatch.setattr("higor_agent.tools.search.arxiv_search", arxiv)
        await kit.search_papers("tight binding", venues=["Physical Review B"])
        assert calls["query"] == '"tight binding"'
        assert calls["venues"] == ["Physical Review B"]
        assert "arxiv" not in calls

    async def test_an_operator_query_is_passed_through(self, kit, monkeypatch):
        calls = {}

        async def s2(query, **kw):
            calls["query"] = query
            return []

        async def arxiv(*a, **k):
            return []

        monkeypatch.setattr("higor_agent.tools.search.s2_search", s2)
        monkeypatch.setattr("higor_agent.tools.search.arxiv_search", arxiv)
        await kit.search_papers('"dft" | "tight binding"')
        assert calls["query"] == '"dft" | "tight binding"'


class TestRelated:
    async def test_unknown_refs_are_rejected(self, kit):
        with pytest.raises(ValueError, match="no known papers"):
            await kit.related_papers(["#99"])

    async def test_seeds_are_excluded_and_the_cites_flag_is_cleared(self, kit, monkeypatch):
        seed = kit.session.register(make_paper(1))
        other = make_paper(2, id="s2:other", cites_author_paper="My DFT paper")

        async def recs(ids, **kw):
            return [other]

        async def cites(pairs, **kw):
            return [make_paper(1)]  # the seed itself comes back

        monkeypatch.setattr("higor_agent.tools.search.s2_recommendations", recs)
        monkeypatch.setattr("higor_agent.tools.search.s2_citations_of", cites)
        out = await kit.related_papers([seed.handle])
        assert "1 related papers" in out
        assert kit.session.papers["s2:other"].cites_author_paper == ""


class TestGetPapers:
    async def test_reports_unknown_refs_alongside_the_known_ones(self, kit):
        kit.session.register(make_paper(1))
        out = await kit.get_papers(["#1", "#42"])
        assert "Unknown refs: #42" in out
        assert "tight binding" in out

    async def test_caps_the_batch(self, kit):
        for i in range(60):
            kit.session.register(make_paper(i, id=f"s2:{i}"))
        out = await kit.get_papers([f"#{i}" for i in range(1, 61)])
        assert out.count("https://www.semanticscholar.org/paper/") <= 40


class TestVenueRatings:
    async def test_reports_unrated_journals_and_skips_preprints(self, kit):
        kit.session.register(make_paper(1, journal="Physical Review B"))
        kit.session.register(make_paper(2, journal="arXiv", arxiv_id="1"))
        out = await kit.venues_needing_rating(["#1", "#2"])
        assert "Physical Review B" in out
        assert "arXiv" not in out

    async def test_says_nothing_to_do_once_cached(self, kit):
        kit.session.register(make_paper(1, journal="Physical Review B"))
        await kit.set_venue_ratings({"Physical Review B": 0.9})
        assert "No unrated venues" in await kit.venues_needing_rating(["#1"])

    async def test_set_ratings_persists_and_reports(self, kit):
        out = await kit.set_venue_ratings({"Physical Review B": 0.9})
        assert "Cached 1 venue ratings" in out
        assert prestige.load_ranks()["physical review b"] == 0.9

    async def test_set_ratings_with_nothing_valid_is_honest(self, kit):
        assert "nothing cached" in await kit.set_venue_ratings({"": 1})

    async def test_falls_back_to_the_candidate_set_when_no_refs_given(self, kit):
        kit.session.candidates = [kit.session.register(make_paper(1, journal="Obscure Letters"))]
        assert "Obscure Letters" in await kit.venues_needing_rating()


class TestShow:
    async def test_publishes_blends_and_warns_about_unrated_venues(self, kit):
        await kit.set_venue_ratings({"Physical Review B": 0.9})
        kit.session.register(make_paper(1, journal="Physical Review B"))
        kit.session.register(make_paper(2, journal="Obscure Letters"))
        out = await kit.show(
            [
                Judgement(ref="#1", relevance=0.8, summary="strong"),
                Judgement(ref="#2", relevance=0.8, summary="weak"),
            ],
            note="week 1",
        )
        assert "Browser now shows 2 papers" in out
        assert "Obscure Letters" in out  # flagged as neutral-by-default
        restored, _ = store.load_last_session()
        assert restored[0].journal == "Physical Review B"

    async def test_unknown_refs_are_skipped_not_fatal(self, kit):
        kit.session.register(make_paper(1))
        out = await kit.show(
            [
                Judgement(ref="#1", relevance=0.5, summary="s"),
                Judgement(ref="#99", relevance=0.5, summary="ghost"),
            ]
        )
        assert "Unknown refs skipped: #99" in out

    async def test_all_refs_unknown_is_an_error(self, kit):
        with pytest.raises(ValueError, match="no known papers to show"):
            await kit.show([Judgement(ref="#99", relevance=0.5, summary="x")])

    async def test_append_mode_keeps_the_previous_list(self, kit):
        kit.session.register(make_paper(1))
        kit.session.register(make_paper(2))
        await kit.show([Judgement(ref="#1", relevance=0.9, summary="a")])
        out = await kit.show(
            [Judgement(ref="#2", relevance=0.4, summary="b")], mode="append"
        )
        assert "shows 2 papers" in out

    async def test_relevance_is_validated_at_the_boundary(self):
        with pytest.raises(ValueError):
            Judgement(ref="#1", relevance=1.5, summary="x")


class TestUpdateProfile:
    async def test_adds_and_removes_list_entries_and_persists(self, kit):
        out = await kit.update_profile(
            add_primary_topics=["xTB"], remove_primary_topics=["tight binding"]
        )
        assert "xTB" in out
        saved = load_profile()
        assert "xTB" in saved.primary_topics
        assert "tight binding" not in saved.primary_topics

    async def test_untouched_lists_are_left_alone(self, kit):
        await kit.update_profile(add_primary_topics=["xTB"])
        saved = load_profile()
        assert saved.prominent_journals == ["Physical Review B"]
        assert saved.secondary_topics == ["GPU acceleration"]

    async def test_scalars_are_clamped(self, kit):
        await kit.update_profile(set_impact_weight=-1, set_lookback_days=0, set_max_papers=0)
        saved = load_profile()
        assert saved.impact_weight == 0.0
        assert saved.lookback_days == 7  # 0 is falsy, so the profile value stands
        assert saved.max_papers_per_week == 5

    async def test_an_empty_edit_changes_nothing(self, kit):
        out = await kit.update_profile()
        assert "Nothing to change" in out

    async def test_removal_is_case_insensitive(self, kit):
        await kit.update_profile(remove_journals=["PHYSICAL REVIEW B"])
        assert load_profile().prominent_journals == []
