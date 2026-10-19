"""Filesystem locations. All state lives in a workspace's ``.higor/`` directory.

A workspace is the folder the researcher runs their agent in. Commands find its
``.higor/`` the way git finds ``.git``: in the current directory or the nearest parent.
``higor-agent install`` creates it; so does ``higor-agent profile`` when run outside one.
The ``HIGOR_DIR`` environment variable points at a state directory directly instead.

Paths are exposed as functions so tests can redirect the whole tree by monkeypatching
``higor_agent.paths.HIGOR_DIR`` once.
"""
from __future__ import annotations

import os
from pathlib import Path

from higor_agent.models import WorkspaceError

WORKSPACE_DIR = ".higor"

HIGOR_DIR: Path | None = None  # set to force a state directory; tests do


def find_state_dir(start: Path | None = None) -> Path | None:
    """The state directory in force here, or ``None`` outside any workspace.

    The search stops below the home directory, so a ``~/.higor`` left from another tool
    never captures every folder under home.
    """
    if HIGOR_DIR is not None:
        return HIGOR_DIR
    if os.environ.get("HIGOR_DIR"):
        return Path(os.environ["HIGOR_DIR"]).expanduser()
    here = (start or Path.cwd()).resolve()
    home = Path.home().resolve()
    for folder in (here, *here.parents):
        if folder == home:
            break
        if (folder / WORKSPACE_DIR).is_dir():
            return folder / WORKSPACE_DIR
    return None


def state_dir() -> Path:
    found = find_state_dir()
    if found is None:
        raise WorkspaceError(
            f"no higor workspace in {Path.cwd()} or its parents. Run the agent from the "
            "folder you installed higor into, or create one there with "
            "`higor-agent install claude-code --dest .`"
        )
    return found


def higor_dir() -> Path:
    """The state directory, created if it is set but missing."""
    path = state_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def init_workspace(folder: Path) -> Path:
    """Make *folder* a workspace. Returns its state directory."""
    path = folder / WORKSPACE_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def profile_path() -> Path:
    return state_dir() / "profile.toml"


def journal_ranks_path() -> Path:
    return state_dir() / "journal_ranks.json"


def reading_log_path() -> Path:
    return state_dir() / "reading_log.jsonl"


def last_session_path() -> Path:
    return state_dir() / "last_session.json"


def session_cache_path() -> Path:
    return state_dir() / "session_cache.json"


def lock_path() -> Path:
    return state_dir() / "session.lock"


def log_path() -> Path:
    return state_dir() / "higor-agent.log"


def agent_dir() -> Path:
    """The ``agent/`` directory of this checkout, holding the instruction file.

    Resolved from this module rather than packaged as data: the commands are always run out
    of a checkout (``uv run --project <repo> higor-agent ...``), and keeping the
    instructions as an editable top-level file beats burying them in the package.
    """
    path = Path(__file__).resolve().parent.parent / "agent"
    if not path.is_dir():
        raise FileNotFoundError(
            f"agent instructions not found at {path}; run higor-agent from a checkout"
        )
    return path
