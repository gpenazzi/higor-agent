"""The session saved between commands."""
import os
import time

from higor_agent import paths, workspace
from higor_agent.session import Session
from tests.conftest import make_paper, make_profile


def saved_session() -> Session:
    s = Session(profile=make_profile(), shown=[])
    s.candidates = [s.register(make_paper(i)) for i in range(3)]
    s.shown = [s.papers[s.candidates[1].id]]
    workspace.save_session(s)
    return s


def test_a_round_trip_keeps_papers_handles_candidates_and_shown():
    before = saved_session()
    after = workspace.load_session()
    assert [after.handle(p) for p in after.candidates] == ["#1", "#2", "#3"]
    assert after.resolve("#2").title == before.resolve("#2").title
    assert [p.id for p in after.shown] == [before.candidates[1].id]


def test_new_papers_continue_the_handle_sequence():
    saved_session()
    s = workspace.load_session()
    assert s.handle(s.register(make_paper(99))) == "#4"


def test_an_idle_cache_starts_over():
    saved_session()
    old = time.time() - workspace.SESSION_TTL - 60
    os.utime(paths.session_cache_path(), (old, old))
    assert workspace.load_session().resolve("#1") is None


def test_a_corrupt_cache_starts_over():
    paths.higor_dir()
    paths.session_cache_path().write_text("{ nope")
    assert workspace.load_session().papers == {}


def test_open_session_saves_even_when_the_command_fails():
    saved_session()
    try:
        with workspace.open_session() as s:
            s.register(make_paper(42))
            raise ValueError("bad call")
    except ValueError:
        pass
    assert workspace.load_session().resolve("#4") is not None
