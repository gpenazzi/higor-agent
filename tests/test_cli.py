"""The command line: argument wiring only, no harness and no model."""
import json

import pytest

from higor_agent import paths
from higor_agent.cli import build_parser, cmd_digest_prompt, cmd_profile, cmd_view, setup_logging
from higor_agent.profile import save_profile
from tests.conftest import make_profile


def parse(*argv):
    return build_parser().parse_args(list(argv))


@pytest.mark.parametrize("argv,expected", [
    (["context"], "context"),
    (["candidates", "--page", "2"], "candidates"),
    (["search", "tight binding", "--venue", "PRB"], "search"),
    (["related", "#1", "#2"], "related"),
    (["papers", "#1"], "papers"),
    (["venues"], "venues"),
    (["rate-venues"], "rate-venues"),
    (["show", "--append"], "show"),
    (["update-profile", "--add-journal", "PRB"], "update-profile"),
    (["view"], "view"),
    (["view", "--no-follow"], "view"),
    (["profile"], "profile"),
    (["profile", "--show"], "profile"),
    (["install", "claude-code"], "install"),
    (["install", "agents-md", "--dest", "/tmp"], "install"),
    (["digest-prompt"], "digest-prompt"),
])
def test_every_subcommand_is_reachable(argv, expected):
    assert parse(*argv).command == expected


def test_a_command_is_required():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_install_rejects_an_unknown_harness():
    with pytest.raises(SystemExit):
        parse("install", "emacs")


def test_view_refuses_without_a_profile(capsys):
    assert cmd_view(parse("view")) == 1
    assert "No profile yet" in capsys.readouterr().out


def test_profile_show_refuses_without_a_profile(capsys):
    assert cmd_profile(parse("profile", "--show")) == 1
    assert "No profile yet" in capsys.readouterr().out


def test_profile_show_prints_the_saved_profile(capsys):
    save_profile(make_profile())
    assert cmd_profile(parse("profile", "--show")) == 0
    assert "Ada" in capsys.readouterr().out


def test_digest_prompt_uses_the_profile_numbers(capsys):
    save_profile(make_profile(max_papers_per_week=5, lookback_days=14))
    assert cmd_digest_prompt(parse("digest-prompt")) == 0
    out = capsys.readouterr().out
    assert "last 14 days" in out
    assert "best 5 papers" in out


def test_digest_prompt_window_can_be_overridden(capsys):
    save_profile(make_profile(lookback_days=7))
    cmd_digest_prompt(parse("digest-prompt", "--days", "30"))
    assert "last 30 days" in capsys.readouterr().out


def test_state_lives_under_the_redirected_directory(isolated_higor_dir):
    assert paths.profile_path().is_relative_to(isolated_higor_dir)
    assert paths.last_session_path().is_relative_to(isolated_higor_dir)
    assert paths.log_path().is_relative_to(isolated_higor_dir)


def test_logging_setup_creates_the_state_directory(isolated_higor_dir):
    """A first run has no ~/.higor. basicConfig opens the file eagerly, so if the
    directory is missing the first command dies before it can report why."""
    assert not isolated_higor_dir.exists()
    setup_logging()
    assert isolated_higor_dir.exists()
    assert paths.log_path().parent.exists()


# ── Tool commands, end to end through the saved session ──────────────────────

@pytest.fixture
def offline(monkeypatch):
    """A saved profile and a sweep that never touches the network."""
    from higor_agent.session import Session
    from tests.conftest import make_paper

    save_profile(make_profile())

    async def sweep(self, days):
        return [self.register(make_paper(i)) for i in range(3)], "topics: 3"

    async def no_fill(self, papers):
        return None

    monkeypatch.setattr(Session, "sweep", sweep)
    monkeypatch.setattr(Session, "fill_details", no_fill)


def run(*argv):
    args = parse(*argv)
    return args.func(args)


def test_handles_survive_between_commands(offline, capsys):
    assert run("candidates") == 0
    assert "#3" in capsys.readouterr().out
    run("papers", "#2")
    assert "Paper 1:" in capsys.readouterr().out  # make_paper(1) got handle #2


def test_show_reads_judgements_from_stdin(offline, capsys, monkeypatch):
    import io
    from higor_agent import store

    run("candidates")
    judgements = [{"ref": "#1", "relevance": 0.9, "summary": "Good."}]
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(judgements)))
    assert run("show", "--note", "week") == 0
    assert "Browser now shows 1 papers" in capsys.readouterr().out
    papers, _ = store.load_last_session()
    assert papers[0].handle == "#1"


def test_show_rejects_bad_json(offline, monkeypatch):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("[{not json"))
    with pytest.raises(ValueError, match="invalid JSON"):
        run("show")


def test_rate_venues_reads_a_file(offline, tmp_path, capsys):
    source = tmp_path / "ratings.json"
    source.write_text(json.dumps({"Physical Review B": 0.8}))
    assert run("rate-venues", str(source)) == 0
    assert "Cached 1 venue ratings" in capsys.readouterr().out


def test_update_profile_maps_the_flags(offline, capsys):
    run("update-profile", "--add-journal", "Nano Letters", "--set-max-papers", "9")
    from higor_agent.profile import load_profile

    profile = load_profile()
    assert "Nano Letters" in profile.prominent_journals
    assert profile.max_papers_per_week == 9


def test_main_reports_a_bad_call_on_stderr(offline, monkeypatch, capsys):
    from higor_agent.cli import main

    monkeypatch.setattr("sys.argv", ["higor-agent", "papers"])  # argparse: refs required
    with pytest.raises(SystemExit):
        main()
    monkeypatch.setattr("sys.argv", ["higor-agent", "related", "#99"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    assert "no known papers" in capsys.readouterr().err
