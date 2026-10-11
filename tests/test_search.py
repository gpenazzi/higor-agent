from datetime import datetime, timedelta, timezone

import httpx
import pytest

from higor_agent import search
from higor_agent.models import SearchError
from tests.conftest import make_paper

# Dated relative to now: a hard-coded date silently ages out of every "last N days"
# filter and the suite starts failing on a calendar, not on a change.
RECENT = datetime.now(tz=timezone.utc) - timedelta(days=2)
RECENT_DAY = RECENT.strftime("%Y-%m-%d")

S2_ITEM = {
    "paperId": "abc",
    "title": "  A   spaced\ntitle ",
    "abstract": None,
    "tldr": {"text": "Short gist."},
    "authors": [{"name": "A. Author"}, {"name": "B. Author"}],
    "publicationVenue": {"name": None},
    "venue": "Journal of Testing",
    "publicationDate": RECENT_DAY,
    "externalIds": {"ArXiv": "2609.01234", "DOI": "10.1/xyz"},
    "citationCount": 3,
}

ARXIV_FEED = f"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2609.01234v2</id>
    <published>{RECENT:%Y-%m-%dT%H:%M:%SZ}</published>
    <title>A spaced
      title</title>
    <summary>Long   abstract
    text.</summary>
    <author><name>A. Author</name></author>
    <arxiv:primary_category term="cond-mat.mtrl-sci"/>
    <arxiv:doi>10.1/xyz</arxiv:doi>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2601.00001v1</id>
    <published>2026-01-01T00:00:00Z</published>
    <title>Old one</title><summary>x</summary>
  </entry>
</feed>"""


class TestParsers:
    def test_s2_item_normalises_title_venue_and_tldr_fallback(self):
        p = search._s2_item_to_paper(S2_ITEM)
        assert p.id == "s2:abc" and p.title == "A spaced title"
        assert p.journal == "Journal of Testing"
        assert p.abstract.startswith("(TL;DR)")
        assert p.arxiv_id == "2609.01234" and p.doi == "10.1/xyz"
        assert p.url == "https://arxiv.org/abs/2609.01234"
        assert p.published == datetime(RECENT.year, RECENT.month, RECENT.day, tzinfo=timezone.utc)
        assert p.citation_count == 3

    def test_s2_item_without_id_or_title_is_dropped(self):
        assert search._s2_item_to_paper({"paperId": "", "title": "x"}) is None
        assert search._s2_item_to_paper({"paperId": "x", "title": None}) is None

    def test_s2_item_tolerates_string_venue_and_odd_authors(self):
        item = dict(S2_ITEM, publicationVenue="4e072790-uuid", authors=["stray", {"name": "Ok"}])
        p = search._s2_item_to_paper(item)
        assert p.journal == "Journal of Testing" and p.authors == ["Ok"]

    def test_s2_year_only_fallback_date(self):
        p = search._s2_item_to_paper({"paperId": "x", "title": "t", "year": 2025})
        assert p.published.year == 2025

    def test_arxiv_feed_parse(self):
        papers = search.parse_arxiv_feed(ARXIV_FEED)
        assert [p.id for p in papers] == ["arxiv:2609.01234", "arxiv:2601.00001"]
        p = papers[0]
        assert p.title == "A spaced title" and p.abstract == "Long abstract text."
        assert p.journal == "arXiv (cond-mat.mtrl-sci)" and p.doi == "10.1/xyz"
        assert p.published.tzinfo is not None

    def test_arxiv_malformed_xml_raises_search_error(self):
        with pytest.raises(SearchError):
            search.parse_arxiv_feed("<feed>")


class TestQueries:
    def test_phrase_query_quotes_and_ors(self):
        assert search.phrase_query(['tight binding', 'a "quoted" one', " "]) == '"tight binding" | "a quoted one"'

    def test_arxiv_query_combines_categories_phrases_and_dates(self):
        q = search.arxiv_query(["dft"], ["cond-mat.mtrl-sci", "physics.comp-ph"], days=7)
        assert q.startswith("(cat:cond-mat.mtrl-sci OR cat:physics.comp-ph) AND (all:\"dft\") AND submittedDate:[")
        assert "TO" in q

    def test_arxiv_query_without_days_or_categories(self):
        assert search.arxiv_query(["dft"], [], None) == '(all:"dft")'


class TestMerge:
    def test_s2_record_wins_but_takes_arxiv_abstract(self):
        s2 = make_paper(1, abstract="", arxiv_id="2609.1", journal="PRB", citation_count=2)
        ax = make_paper(2, id="arxiv:2609.1", arxiv_id="2609.1", abstract="From arXiv", journal="arXiv")
        merged = search.merge_papers([ax], [s2])
        assert len(merged) == 1
        assert merged[0].id == s2.id and merged[0].abstract == "From arXiv" and merged[0].journal == "PRB"

    def test_dedup_by_doi_and_title(self):
        a = make_paper(1, doi="10.1/A", title="Same Title!")
        b = make_paper(2, doi="10.1/a", title="Different")
        c = make_paper(3, title="same title")
        assert [p.id for p in search.merge_papers([a, b, c])] == [a.id]

    def test_cites_flag_survives_merge(self):
        a = make_paper(1)
        b = make_paper(1, cites_author_paper="Mine")
        assert search.merge_papers([a], [b])[0].cites_author_paper == "Mine"

    def test_within_treats_undated_as_recent(self):
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=7)
        assert search.within(make_paper(1, published=None), cutoff)
        assert not search.within(make_paper(1, published=datetime(2020, 1, 1)), cutoff)


class TestHttp:
    async def test_s2_search_passes_filters_and_paginates(self, httpx_mock):
        httpx_mock.add_response(json={"data": [dict(S2_ITEM, paperId="1")], "token": "t2"})
        httpx_mock.add_response(json={"data": [dict(S2_ITEM, paperId="2")]})
        papers = await search.s2_search('"dft"', days=7, fields_of_study=["Physics"], venues=["PRB"])
        assert [p.id for p in papers] == ["s2:1", "s2:2"]
        first = httpx_mock.get_requests()[0]
        assert first.url.params["fieldsOfStudy"] == "Physics"
        assert first.url.params["venue"] == "PRB"
        assert ":" in first.url.params["publicationDateOrYear"]
        assert httpx_mock.get_requests()[1].url.params["token"] == "t2"

    async def test_s2_retries_on_429_then_succeeds(self, httpx_mock, monkeypatch):
        monkeypatch.setattr("higor_agent.search._S2_RETRY_DELAYS", (0.0,))
        httpx_mock.add_response(status_code=429)
        httpx_mock.add_response(json={"data": []})
        assert await search.s2_search("x", days=7) == []

    async def test_s2_persistent_429_raises(self, httpx_mock, monkeypatch):
        monkeypatch.setattr("higor_agent.search._S2_RETRY_DELAYS", (0.0,))
        httpx_mock.add_response(status_code=429)
        httpx_mock.add_response(status_code=429)
        with pytest.raises(SearchError):
            await search.s2_search("x", days=7)

    async def test_http_error_becomes_search_error(self, httpx_mock):
        httpx_mock.add_exception(httpx.ConnectError("boom"))
        with pytest.raises(SearchError):
            await search.s2_recommendations(["a"], days=7)

    async def test_citations_tag_cited_title_and_filter_dates(self, httpx_mock):
        old = dict(S2_ITEM, paperId="old", publicationDate="2020-01-01")
        httpx_mock.add_response(json={"data": [{"citingPaper": S2_ITEM}, {"citingPaper": old}]})
        papers = await search.s2_citations_of([("abc123", "My DFT paper")], days=30)
        assert [p.id for p in papers] == ["s2:abc"]
        assert papers[0].cites_author_paper == "My DFT paper"

    async def test_recommendations_filter_by_date_when_days_given(self, httpx_mock):
        old = dict(S2_ITEM, paperId="old", publicationDate="2020-01-01")
        httpx_mock.add_response(json={"recommendedPapers": [S2_ITEM, old]})
        assert [p.id for p in await search.s2_recommendations(["x"], days=30)] == ["s2:abc"]
        httpx_mock.add_response(json={"recommendedPapers": [S2_ITEM, old]})
        assert len(await search.s2_recommendations(["x"], days=None)) == 2

    async def test_s2_fetch_requests_tldr_and_skips_nulls(self, httpx_mock):
        httpx_mock.add_response(json=[S2_ITEM, None])
        papers = await search.s2_fetch(["abc", "missing"])
        assert len(papers) == 1 and "tldr" in httpx_mock.get_requests()[0].url.params["fields"]

    async def test_arxiv_search_filters_window(self, httpx_mock):
        httpx_mock.add_response(text=ARXIV_FEED)
        papers = await search.arxiv_search(["dft"], ["cond-mat.mtrl-sci"], days=30000)
        assert len(papers) == 2
        assert "search_query" in str(httpx_mock.get_requests()[0].url)

    async def test_arxiv_search_noop_without_inputs(self, httpx_mock):
        assert await search.arxiv_search([], [], days=7) == []

    async def test_author_papers_and_topics(self, httpx_mock):
        httpx_mock.add_response(json={"data": [
            {"paperId": "p1", "title": "One", "year": 2026, "citationCount": 1,
             "s2FieldsOfStudy": [{"category": "Physics"}]},
            {"paperId": "p2", "title": "Two", "year": 2010, "citationCount": 900,
             "s2FieldsOfStudy": [{"category": "Physics"}, {"category": "Chemistry"}]},
        ]})
        pairs, fields = await search.fetch_author_papers_and_topics("42")
        assert pairs == [("p1", "One"), ("p2", "Two")]
        assert fields == ["Physics", "Chemistry"]

    async def test_crossref_fills_missing_authors_and_abstract(self, httpx_mock):
        httpx_mock.add_response(
            url="https://api.crossref.org/works/10.1/x",
            json={"message": {
                "author": [{"given": "Ada", "family": "Lovelace"}, {"name": "Consortium"}, "junk"],
                "abstract": "<jats:p>Full   <i>text</i>.</jats:p>",
            }},
        )
        no_authors = make_paper(1, doi="10.1/x", authors=[], abstract="")
        complete = make_paper(2, doi="10.1/y")
        no_doi = make_paper(3, authors=[])
        out = await search.crossref_fill([no_authors, complete, no_doi])
        assert out[0].authors == ["Ada Lovelace", "Consortium"] and out[0].abstract == "Full text ."
        assert out[1] is complete and out[2] is no_doi
        assert len(httpx_mock.get_requests()) == 1
        assert "higor" in httpx_mock.get_requests()[0].headers["User-Agent"]

    async def test_crossref_failure_leaves_paper_untouched(self, httpx_mock):
        httpx_mock.add_response(status_code=404)
        p = make_paper(1, doi="10.1/missing", authors=[])
        assert (await search.crossref_fill([p]))[0] is p

    async def test_api_key_header_when_env_set(self, httpx_mock, monkeypatch):
        monkeypatch.setenv("S2_API_KEY", "k")
        httpx_mock.add_response(json={"data": []})
        await search.s2_search("x", days=1)
        assert httpx_mock.get_requests()[0].headers["x-api-key"] == "k"
