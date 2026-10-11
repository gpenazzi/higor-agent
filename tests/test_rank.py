import pytest

from higor_agent.rank import blend_score, citation_norm, is_preprint, score_papers
from tests.conftest import make_paper


def test_blend_neutral_uncited_is_identity():
    assert blend_score(0.6, 0.5, 0.0, 0.25) == pytest.approx(0.6)


def test_blend_boosts_prestige_and_penalises_weak_venues():
    assert blend_score(0.6, 1.0, 0.0, 0.25) > 0.6 > blend_score(0.6, 0.0, 0.0, 0.25)


def test_blend_never_rescues_off_topic_and_clamps():
    assert blend_score(0.0, 1.0, 1.0, 1.0) == 0.0
    assert blend_score(1.0, 1.0, 1.0, 1.0) == 1.0


def test_citation_norm_log_scale():
    assert citation_norm(0) == 0.0
    assert 0 < citation_norm(9) < citation_norm(99) < citation_norm(999) <= 1.0


@pytest.mark.parametrize("journal,url,arxiv_id,expected", [
    ("arXiv (cond-mat)", "", "", True),
    ("bioRxiv", "", "", True),
    ("", "https://arxiv.org/abs/1", "", True),
    ("", "", "2609.1", True),
    ("Physical Review B", "https://arxiv.org/abs/1", "2609.1", False),
    ("", "https://doi.org/x", "", False),
])
def test_is_preprint(journal, url, arxiv_id, expected):
    assert is_preprint(make_paper(1, journal=journal, url=url, arxiv_id=arxiv_id)) is expected


def test_score_papers_blends_sorts_and_keeps_preprints_neutral():
    prb = make_paper(1, journal="Physical Review B")
    weak = make_paper(2, journal="Predatory Letters")
    pre = make_paper(3, journal="arXiv", arxiv_id="1")
    prestige = {"physical review b": 0.9, "predatory letters": 0.1}
    out = score_papers([(weak, 0.7, " w "), (prb, 0.7, "p"), (pre, 0.7, "a")], prestige, 0.25)
    assert [p.id for p in out] == [prb.id, pre.id, weak.id]
    assert out[1].venue_prestige == 0.5 and out[1].topical_relevance == 0.7
    assert out[2].summary == "w"


def test_score_papers_clamps_relevance():
    out = score_papers([(make_paper(1), 1.7, "s")], {}, 0.0)
    assert out[0].topical_relevance == 1.0
