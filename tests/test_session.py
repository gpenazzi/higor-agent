"""Session state: handles, the merging paper cache, sweeps and the snapshot."""
import pytest

from higor_agent import store
from higor_agent.models import Paper, SearchError
from higor_agent.session import Session
from tests.conftest import make_paper, make_profile


def _session(**kwargs) -> Session:
    return Session(profile=make_profile(**kwargs), shown=[])


class TestHandles:
    def test_handles_are_assigned_in_order_and_written_onto_the_paper(self):
        s = _session()
        first = s.register(make_paper(1))
        second = s.register(make_paper(2))
        assert (first.handle, second.handle) == ("#1", "#2")

    def test_a_paper_keeps_its_handle_when_re_registered(self):
        s = _session()
        s.register(make_paper(1))
        s.register(make_paper(2))
        again = s.register(make_paper(1, abstract="now with an abstract"))
        assert again.handle == "#1"
        assert s.papers[again.id].abstract == "now with an abstract"

    def test_re_registering_merges_rather_than_overwrites(self):
        s = _session()
        s.register(make_paper(1, abstract="kept", citation_count=9))
        merged = s.register(make_paper(1, abstract="", citation_count=0, journal="Nature"))
        assert merged.abstract == "kept"
        assert merged.citation_count == 9
        assert merged.journal == "Nature"

    @pytest.mark.parametrize("ref", ["#1", "1", " #1 "])
    def test_resolve_accepts_handle_spellings(self, ref):
        s = _session()
        paper = s.register(make_paper(1))
        assert s.resolve(ref) is not None
        assert s.resolve(ref).id == paper.id

    def test_resolve_accepts_raw_and_bare_ids(self):
        s = _session()
        paper = s.register(make_paper(1))
        bare = paper.id.removeprefix("s2:")
        assert s.resolve(paper.id).id == paper.id
        assert s.resolve(bare).id == paper.id

    @pytest.mark.parametrize("ref", ["", "   ", "#99", "nonsense"])
    def test_resolve_returns_none_for_unknown_refs(self, ref):
        assert _session().resolve(ref) is None

    def test_restores_shown_papers_from_the_snapshot(self):
        store.save_last_session([make_paper(1), make_paper(2)], "last week")
        s = Session(profile=make_profile())
        assert len(s.shown) == 2
        assert [p.handle for p in s.shown] == ["#1", "#2"]


class TestRendering:
    def test_table_marks_papers_that_cite_the_researcher(self):
        s = _session()
        s.register(make_paper(1, cites_author_paper="My DFT paper"))
        s.register(make_paper(2))
        rows = s.table([s.papers[p] for p in s.papers]).splitlines()
        assert " C " in rows[0]
        assert " C " not in rows[1]

    def test_table_handles_an_undated_paper(self):
        s = _session()
        s.register(make_paper(1, published=None))
        assert "????-??-??" in s.table(list(s.papers.values()))

    def test_empty_table_says_so(self):
        assert _session().table([]) == "(none)"

    def test_context_block_carries_profile_history_and_shown(self):
        store.save_last_session([make_paper(1)], "note")
        block = Session(profile=make_profile()).context_block()
        assert "RESEARCHER PROFILE" in block
        assert "READING HISTORY" in block
        assert "PAPERS CURRENTLY SHOWN" in block
        assert "tight binding" in block


class TestSweep:
    async def test_sweep_merges_sources_and_reports_counts(self, monkeypatch):
        async def s2(*a, **k):
            return [make_paper(1)]

        async def arxiv(*a, **k):
            return [make_paper(2, id="arxiv:2", arxiv_id="2")]

        async def cites(*a, **k):
            return [make_paper(3, cites_author_paper="My DFT paper")]

        async def recs(*a, **k):
            return []

        monkeypatch.setattr("higor_agent.session.search.s2_search", s2)
        monkeypatch.setattr("higor_agent.session.search.arxiv_search", arxiv)
        monkeypatch.setattr("higor_agent.session.search.s2_citations_of", cites)
        monkeypatch.setattr("higor_agent.session.search.s2_recommendations", recs)

        papers, note = await _session().sweep(7)
        # Five sources ran; the journal sweep returned the same paper as the topic sweep,
        # so the merge leaves three distinct papers.
        assert len(papers) == 3
        assert "citations: 1" in note
        assert "recommendations: 0" in note
        # Papers citing the researcher sort first.
        assert papers[0].cites_author_paper == "My DFT paper"

    async def test_one_failing_source_does_not_sink_the_sweep(self, monkeypatch):
        async def ok(*a, **k):
            return [make_paper(1)]

        async def boom(*a, **k):
            raise SearchError("arXiv is down")

        monkeypatch.setattr("higor_agent.session.search.s2_search", ok)
        monkeypatch.setattr("higor_agent.session.search.arxiv_search", boom)
        monkeypatch.setattr("higor_agent.session.search.s2_citations_of", ok)
        monkeypatch.setattr("higor_agent.session.search.s2_recommendations", ok)

        papers, note = await _session().sweep(7)
        assert papers
        assert "arXiv: failed" in note

    async def test_sweep_with_no_configured_sources_says_so(self):
        session = Session(
            profile=make_profile(
                primary_topics=[], secondary_topics=[], prominent_journals=[], own_paper_ids=[]
            ),
            shown=[],
        )
        papers, note = await session.sweep(7)
        assert papers == []
        assert "no sources" in note


class TestApplyShow:
    async def test_blends_sorts_and_writes_the_snapshot(self, monkeypatch):
        monkeypatch.setattr(Session, "fill_details", lambda self, papers: _noop())
        from higor_agent import prestige

        prestige.set_ratings({"Physical Review B": 0.9})
        s = _session()
        strong = s.register(make_paper(1, journal="Physical Review B"))
        weak = s.register(make_paper(2, journal="Obscure Letters"))
        shown, unrated = await s.apply_show(
            [(weak, 0.8, "w"), (strong, 0.8, "s")], note="week 1"
        )
        assert [p.id for p in shown] == [strong.id, weak.id]
        assert unrated == ["Obscure Letters"]
        restored, _ = store.load_last_session()
        assert [p.handle for p in restored] == ["#1", "#2"]
        assert restored[0].summary == "s"

    async def test_append_keeps_earlier_papers(self, monkeypatch):
        monkeypatch.setattr(Session, "fill_details", lambda self, papers: _noop())
        s = _session()
        first = s.register(make_paper(1))
        await s.apply_show([(first, 0.9, "a")])
        second = s.register(make_paper(2))
        shown, _ = await s.apply_show([(second, 0.4, "b")], append=True)
        assert {p.id for p in shown} == {first.id, second.id}

    async def test_replace_drops_earlier_papers(self, monkeypatch):
        monkeypatch.setattr(Session, "fill_details", lambda self, papers: _noop())
        s = _session()
        first = s.register(make_paper(1))
        await s.apply_show([(first, 0.9, "a")])
        second = s.register(make_paper(2))
        shown, _ = await s.apply_show([(second, 0.4, "b")])
        assert [p.id for p in shown] == [second.id]


async def _noop() -> None:
    return None


class TestFillDetails:
    async def test_missing_abstracts_are_backfilled(self, monkeypatch):
        s = _session()
        bare = s.register(make_paper(1, abstract=""))

        async def fetch(ids):
            return [Paper(id=bare.id, title="t", abstract="fetched abstract")]

        async def crossref(papers):
            return papers

        monkeypatch.setattr("higor_agent.session.search.s2_fetch", fetch)
        monkeypatch.setattr("higor_agent.session.search.crossref_fill", crossref)
        await s.fill_details([bare])
        assert s.papers[bare.id].abstract == "fetched abstract"

    async def test_a_failing_backfill_is_not_fatal(self, monkeypatch):
        s = _session()
        bare = s.register(make_paper(1, abstract=""))

        async def fetch(ids):
            raise SearchError("down")

        async def crossref(papers):
            return papers

        monkeypatch.setattr("higor_agent.session.search.s2_fetch", fetch)
        monkeypatch.setattr("higor_agent.session.search.crossref_fill", crossref)
        await s.fill_details([bare])
        assert s.papers[bare.id].abstract == ""
