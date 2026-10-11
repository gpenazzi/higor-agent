"""The venue cache: no model anywhere, the host agent supplies the judgements."""
import json

from higor_agent import paths, prestige


def test_unrated_lists_only_unknown_venues_keeping_spelling():
    prestige.save_ranks({"nature physics": 0.95})
    assert prestige.unrated({"Nature Physics", "Weird Journal", "", "  "}) == ["Weird Journal"]


def test_unrated_deduplicates_case_variants():
    assert prestige.unrated(["Nature Physics", "nature physics"]) == ["Nature Physics"]


def test_set_ratings_clamps_coerces_and_persists():
    stored = prestige.set_ratings({"Nature Physics": 1.4, "Weird Journal": "0.3", "Bad": -2})
    assert stored == {"nature physics": 1.0, "weird journal": 0.3, "bad": 0.0}
    assert json.loads(paths.journal_ranks_path().read_text())["nature physics"] == 1.0


def test_set_ratings_skips_junk_without_losing_the_rest():
    assert prestige.set_ratings({"Good": 0.6, "": 0.9, "Bad": "not a number"}) == {"good": 0.6}


def test_set_ratings_merges_into_existing_cache():
    prestige.set_ratings({"A": 0.2})
    prestige.set_ratings({"B": 0.4})
    assert prestige.load_ranks() == {"a": 0.2, "b": 0.4}


def test_prestige_for_is_neutral_on_unrated_venues():
    prestige.set_ratings({"Known": 0.8})
    assert prestige.prestige_for({"Known", "Unknown"}) == {"known": 0.8, "unknown": 0.5}


def test_hand_edited_cache_is_respected():
    paths.higor_dir()
    paths.journal_ranks_path().write_text('{"my journal": 0.77}')
    assert prestige.prestige_for({"My Journal"}) == {"my journal": 0.77}
    assert prestige.unrated({"My Journal"}) == []


def test_unreadable_cache_is_ignored_not_fatal():
    paths.higor_dir()
    paths.journal_ranks_path().write_text("{not json")
    assert prestige.load_ranks() == {}
    assert prestige.prestige_for({"X"}) == {"x": 0.5}


def test_no_venues_means_no_work():
    assert prestige.prestige_for(set()) == {}
    assert prestige.set_ratings({}) == {}
