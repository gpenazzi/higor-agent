"""The viewer: it reads the snapshot, follows changes, and writes the reading log."""
from higor_agent import paths, store
from higor_agent.models import PaperInteraction
from higor_agent.profile import save_profile
from higor_agent.view import ViewerApp, _clipboard_text, _paper_detail_text, resolve_cited_titles
from tests.conftest import make_paper, make_profile


class TestHelpers:
    def test_cited_ids_are_replaced_by_titles(self):
        profile = make_profile(own_paper_titles={"abc123": "My DFT paper"})
        papers = [make_paper(1, cites_author_paper="abc123"), make_paper(2, cites_author_paper="")]
        out = resolve_cited_titles(papers, profile)
        assert out[0].cites_author_paper == "My DFT paper"
        assert out[1].cites_author_paper == ""

    def test_an_unknown_cited_id_is_left_as_is(self):
        out = resolve_cited_titles([make_paper(1, cites_author_paper="zzz")], make_profile())
        assert out[0].cites_author_paper == "zzz"

    def test_clipboard_text_carries_the_citation_essentials(self):
        text = _clipboard_text(make_paper(1, doi="10.1/x"))
        assert "Paper 1" in text
        assert "Alice Smith" in text
        assert "doi:10.1/x" in text
        assert "https://" in text

    def test_detail_shows_the_scoring_signals(self):
        paper = make_paper(1, relevance_score=0.8, topical_relevance=0.7, venue_prestige=0.9)
        text = _paper_detail_text(paper).plain
        assert "score 0.80" in text
        assert "topical 0.70" in text
        assert "venue 0.90" in text

    def test_detail_is_honest_about_a_missing_abstract(self):
        assert "(no abstract available)" in _paper_detail_text(make_paper(1, abstract="")).plain


class TestViewer:
    async def test_shows_the_published_list(self):
        save_profile(make_profile())
        store.save_last_session([make_paper(1), make_paper(2)], "week 1")
        app = ViewerApp(follow=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert len(app.papers) == 2
            assert "week 1" not in app.sub_title  # the note is not the subtitle; the date is
            assert "2 papers" in app.sub_title

    async def test_says_so_when_nothing_has_been_published(self):
        save_profile(make_profile())
        app = ViewerApp(follow=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.papers == []
            assert "no list yet" in app.sub_title

    async def test_following_picks_up_a_new_snapshot(self):
        save_profile(make_profile())
        store.save_last_session([make_paper(1)], "first")
        app = ViewerApp(follow=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert len(app.papers) == 1
            store.save_last_session([make_paper(1), make_paper(2), make_paper(3)], "second")
            await app._poll()
            await pilot.pause()
            assert len(app.papers) == 3

    async def test_opening_the_detail_pane_logs_a_read(self):
        save_profile(make_profile())
        store.save_last_session([make_paper(1)], "")
        app = ViewerApp(follow=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()
        actions = [ix.action for ix in store.recent_interactions()]
        assert "read_full" in actions

    async def test_saving_logs_the_paper_by_title(self):
        save_profile(make_profile())
        store.save_last_session([make_paper(1)], "")
        app = ViewerApp(follow=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
        logged = store.recent_interactions()[0]
        assert logged.action == "saved"
        assert "Paper 1" in logged.paper_title

    async def test_quitting_exits_cleanly(self):
        save_profile(make_profile())
        app = ViewerApp(follow=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("q")
            await pilot.pause()
        assert app.return_value is None


class TestLogRoundTrip:
    def test_the_log_survives_a_reread(self):
        store.log_interaction(PaperInteraction(paper_id="x", paper_title="T", action="saved"))
        assert store.recent_interactions()[0].paper_title == "T"
        assert paths.reading_log_path().exists()


class TestMetaLine:
    def test_missing_authors_leave_no_dangling_separator(self):
        from higor_agent.view import _paper_list_text

        line = _paper_list_text(make_paper(1, authors=[], journal="Nature")).plain
        assert "· Nature" not in line
        assert "Nature · " in line

    def test_a_paper_with_no_metadata_at_all_renders(self):
        from higor_agent.view import _paper_list_text

        line = _paper_list_text(
            make_paper(1, authors=[], journal="", published=None)
        ).plain
        assert "·" not in line

    def test_extra_authors_are_counted(self):
        from higor_agent.view import _paper_list_text

        line = _paper_list_text(make_paper(1)).plain  # four authors
        assert "+1" in line
