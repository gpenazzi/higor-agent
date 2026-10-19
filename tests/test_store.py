import json
from datetime import datetime, timezone

from higor_agent import paths, store
from higor_agent.models import PaperInteraction
from tests.conftest import make_paper


def test_log_and_recent_roundtrip_newest_first():
    for i in range(3):
        store.log_interaction(PaperInteraction(paper_id=f"s2:{i}", paper_title=f"T{i}", action="saved"))
    recent = store.recent_interactions()
    assert [ix.paper_id for ix in recent] == ["s2:2", "s2:1", "s2:0"]
    assert store.recent_interactions(2)[0].paper_id == "s2:2"


def test_recent_skips_malformed_lines():
    path = paths.reading_log_path()
    path.parent.mkdir(parents=True)
    path.write_text('not json\n' + PaperInteraction(paper_id="s2:1", action="opened").model_dump_json() + "\n")
    assert [ix.paper_id for ix in store.recent_interactions()] == ["s2:1"]


def test_last_session_roundtrip():
    assert store.load_last_session() is None
    papers = [make_paper(1, relevance_score=0.9), make_paper(2)]
    store.save_last_session(papers, note="week 36")
    loaded, fetched = store.load_last_session()
    assert [p.id for p in loaded] == [p.id for p in papers]
    assert loaded[0].relevance_score == 0.9
    assert fetched.tzinfo is not None
    assert json.loads(paths.last_session_path().read_text())["note"] == "week 36"
