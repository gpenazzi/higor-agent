"""On-disk state: the reading log and the last shown paper list."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from higor_agent import paths
from higor_agent.models import Paper, PaperInteraction

logger = logging.getLogger(__name__)


def log_interaction(interaction: PaperInteraction) -> None:
    """Append one interaction to ``~/.higor/reading_log.jsonl``."""
    path = paths.reading_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(interaction.model_dump_json() + "\n")
    except OSError as exc:
        logger.warning("Could not write reading log: %s", exc)


def recent_interactions(n: int = 200) -> list[PaperInteraction]:
    """Return the *n* most recent interactions, newest first."""
    path = paths.reading_log_path()
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        logger.warning("Could not read reading log: %s", exc)
        return []
    out: list[PaperInteraction] = []
    for line in reversed(lines[-n:]):
        if not line.strip():
            continue
        try:
            out.append(PaperInteraction.model_validate_json(line))
        except Exception as exc:  # noqa: BLE001
            logger.debug("Skipping malformed log line: %s", exc)
    return out


def save_last_session(papers: list[Paper], note: str = "") -> None:
    """Persist the list currently shown so the next launch can display it instantly."""
    path = paths.last_session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
                "note": note,
                "papers": [p.model_dump(mode="json") for p in papers],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def load_last_session() -> tuple[list[Paper], datetime] | None:
    """Return ``(papers, fetched_at)`` from the last session, or ``None``."""
    path = paths.last_session_path()
    if not path.exists():
        legacy = paths.HIGOR_DIR / "cache" / "last_session.json"  # pre-redesign location
        if not legacy.exists():
            return None
        path = legacy
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        papers = [Paper.model_validate(p) for p in raw["papers"]]
        fetched = datetime.fromisoformat(raw["fetched_at"])
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        return papers, fetched
    except Exception as exc:  # noqa: BLE001
        logger.warning("Last-session read error (%s); ignoring.", exc)
        return None
