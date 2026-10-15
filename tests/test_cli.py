"""The command line: argument wiring only, no harness and no model."""
import pytest

from higor_agent import paths
from higor_agent.cli import build_parser, cmd_digest_prompt, cmd_profile, cmd_view, setup_logging
from higor_agent.profile import save_profile
from tests.conftest import make_profile


def parse(*argv):
    return build_parser().parse_args(list(argv))


@pytest.mark.parametrize("argv,expected", [
    (["serve"], "serve"),
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
    directory is missing the server dies during the host's handshake."""
    assert not isolated_higor_dir.exists()
    setup_logging()
    assert isolated_higor_dir.exists()
    assert paths.log_path().parent.exists()
