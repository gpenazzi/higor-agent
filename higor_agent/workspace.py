"""The session between commands: saved to disk, guarded by a lock.

Each tool command is its own process, so the paper cache and the handle table go to
``session_cache.json`` after every call and come back at the start of the next. The lock
serialises commands, which keeps two of them from writing the cache at once and from
hitting Semantic Scholar in parallel.

A cache left idle for ``SESSION_TTL`` is dropped, so a new conversation the next day
starts with fresh handles instead of ``#3000``.
"""
from __future__ import annotations

import contextlib
import json
import logging
import time
from collections.abc import Iterator

from higor_agent import paths
from higor_agent.session import Session

logger = logging.getLogger(__name__)

SESSION_TTL = 12 * 3600  # idle seconds before handles start over


@contextlib.contextmanager
def _locked() -> Iterator[None]:
    try:
        import fcntl
    except ImportError:  # Windows: no advisory lock, so commands must not overlap
        yield
        return
    paths.higor_dir()
    with open(paths.lock_path(), "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def load_session() -> Session:
    path = paths.session_cache_path()
    try:
        if time.time() - path.stat().st_mtime > SESSION_TTL:
            return Session()
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Session()
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring unreadable session cache %s: %s", path, exc)
        return Session()
    return Session.from_cache(data)


def save_session(session: Session) -> None:
    path = paths.session_cache_path()
    paths.higor_dir()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(session.to_cache()), encoding="utf-8")
    tmp.replace(path)


@contextlib.contextmanager
def open_session() -> Iterator[Session]:
    """Load the session, hand it out, save it back, all under the lock."""
    with _locked():
        session = load_session()
        try:
            yield session
        finally:
            save_session(session)
