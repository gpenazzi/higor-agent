"""Finding the workspace's state directory."""
import pytest

from higor_agent import paths
from higor_agent.models import WorkspaceError


@pytest.fixture
def no_override(monkeypatch):
    monkeypatch.setattr(paths, "HIGOR_DIR", None)
    monkeypatch.delenv("HIGOR_DIR", raising=False)


def test_finds_the_workspace_in_the_current_folder(no_override, tmp_path, monkeypatch):
    state = paths.init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert paths.state_dir() == state.resolve()


def test_finds_the_workspace_from_a_subfolder(no_override, tmp_path, monkeypatch):
    state = paths.init_workspace(tmp_path)
    (tmp_path / "notes" / "2026").mkdir(parents=True)
    monkeypatch.chdir(tmp_path / "notes" / "2026")
    assert paths.profile_path() == state.resolve() / "profile.toml"


def test_outside_a_workspace_commands_refuse(no_override, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert paths.find_state_dir() is None
    with pytest.raises(WorkspaceError, match="no higor workspace"):
        paths.last_session_path()


def test_a_home_level_higor_is_not_a_workspace(no_override, tmp_path, monkeypatch):
    monkeypatch.setattr(paths.Path, "home", lambda: tmp_path)
    paths.init_workspace(tmp_path)
    (tmp_path / "research").mkdir()
    monkeypatch.chdir(tmp_path / "research")
    assert paths.find_state_dir() is None


def test_the_environment_variable_wins(no_override, tmp_path, monkeypatch):
    monkeypatch.setenv("HIGOR_DIR", str(tmp_path / "elsewhere"))
    assert paths.state_dir() == tmp_path / "elsewhere"
