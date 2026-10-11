"""Filesystem locations. Everything lives under ``HIGOR_DIR``, by default ``~/.higor``.

The default is shared with the original single-process Higor on purpose: the profile,
the venue cache and the reading log have the same on-disk schema, so an existing
installation keeps working. Set the ``HIGOR_DIR`` environment variable to use a
separate tree.

Paths are exposed as functions so tests can redirect the whole tree by monkeypatching
``higor_agent.paths.HIGOR_DIR`` once.
"""
from __future__ import annotations

import os
from pathlib import Path

HIGOR_DIR = Path(os.environ.get("HIGOR_DIR") or (Path.home() / ".higor")).expanduser()


def higor_dir() -> Path:
    HIGOR_DIR.mkdir(parents=True, exist_ok=True)
    return HIGOR_DIR


def profile_path() -> Path:
    return HIGOR_DIR / "profile.toml"


def journal_ranks_path() -> Path:
    return HIGOR_DIR / "journal_ranks.json"


def reading_log_path() -> Path:
    return HIGOR_DIR / "reading_log.jsonl"


def last_session_path() -> Path:
    return HIGOR_DIR / "last_session.json"


def log_path() -> Path:
    return HIGOR_DIR / "higor-agent.log"


def agent_dir() -> Path:
    """The ``agent/`` directory of this checkout, holding the instruction file.

    Resolved from this module rather than packaged as data: the server is always run out
    of a checkout (``uv run --directory <repo> higor-agent serve``), and keeping the
    instructions as an editable top-level file beats burying them in the package.
    """
    path = Path(__file__).resolve().parent.parent / "agent"
    if not path.is_dir():
        raise FileNotFoundError(
            f"agent instructions not found at {path}; run higor-agent from a checkout"
        )
    return path
