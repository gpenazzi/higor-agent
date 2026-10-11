"""Textual viewer over the snapshot the ``show`` tool writes.

The original Higor owned the agent and pushed lists into the UI through a callback. Here
the agent lives in whatever harness the researcher is using, and the two processes meet at
``last_session.json``: ``show`` writes it, this watches its mtime and redraws. That makes
the browser optional rather than load-bearing, and it is still the only place the reading
log gets written, since only a human can say which paper they actually opened.

Public interface: ``run_viewer(follow=True) -> None``.
"""
from __future__ import annotations

import shutil
import subprocess
import webbrowser
from datetime import datetime, timezone
from typing import ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, ListItem, ListView, Static

from higor_agent import paths, store
from higor_agent.models import Paper, PaperInteraction, UserProfile
from higor_agent.profile import load_profile

_ASCII_BANNER = (
    " ██╗  ██╗██╗ ██████╗  ██████╗ ██████╗ \n"
    " ██║  ██║██║██╔════╝ ██╔═══██╗██╔══██╗\n"
    " ███████║██║██║  ███╗██║   ██║██████╔╝\n"
    " ██╔══██║██║██║   ██║██║   ██║██╔══██╗\n"
    " ██║  ██║██║╚██████╔╝╚██████╔╝██║  ██║\n"
    " ╚═╝  ╚═╝╚═╝ ╚═════╝  ╚═════╝ ╚═╝  ╚═╝"
)

_ACCENT = "#a78bfa"
_DIM = "#6e7681"
_FG = "#c9d1d9"
POLL_SECONDS = 2.0

_CSS = """
Screen { background: #0d1117; }
#banner { color: #a78bfa; text-align: center; padding: 1 0 0 0; }
#subtitle { color: #6e7681; text-align: center; height: 1; }
ListView {
    height: 1fr; background: #0d1117;
    scrollbar-background: #161b22; scrollbar-color: #6e7681; scrollbar-color-hover: #a78bfa;
}
ListItem { background: #0d1117; border-bottom: solid #161b22; padding: 0 1; }
ListItem:hover { background: #161b22; }
ListItem.--highlight { background: #1e3a5f; border-left: thick #a78bfa; }
#detail {
    height: 16; background: #161b22; border: solid #a78bfa; padding: 0 1;
    display: none; overflow-y: scroll;
}
#detail.visible { display: block; }
Footer { background: #21262d; color: #6e7681; }
"""


def _score_style(score: float) -> str:
    if score >= 0.7:
        return "#00ff00"
    if score >= 0.4:
        return "#cccc00"
    return "#336633"


def _paper_list_text(paper: Paper) -> Text:
    t = Text(no_wrap=False)
    t.append(f" {paper.relevance_score:.2f} ", style=f"bold {_score_style(paper.relevance_score)}")
    if paper.handle:
        t.append(f"{paper.handle} ", style=_DIM)
    t.append(paper.title + "\n", style=f"bold {_FG}")
    # Join only the parts we have: Semantic Scholar leaves authors or venue empty often
    # enough that a fixed layout would show dangling separators.
    parts = []
    if paper.authors:
        extra = f" +{len(paper.authors) - 3}" if len(paper.authors) > 3 else ""
        parts.append(", ".join(paper.authors[:3]) + extra)
    if paper.journal:
        parts.append(paper.journal)
    if paper.published:
        parts.append(f"{paper.published:%Y-%m-%d}")
    if parts:
        t.append("       " + " · ".join(parts) + "\n", style=_DIM)
    if paper.cites_author_paper:
        t.append(f'       cites: "{paper.cites_author_paper}"\n', style=f"{_ACCENT} italic")
    if paper.summary:
        t.append("       " + paper.summary + "\n", style="#8b949e")
    return t


def _paper_detail_text(paper: Paper) -> Text:
    t = Text(no_wrap=False)
    t.append(paper.title + "\n", style=f"bold {_FG}")
    t.append(", ".join(paper.authors) + "\n", style=_DIM)
    line = paper.journal or ""
    if paper.published:
        line += f"  {paper.published:%Y-%m-%d}"
    if paper.doi:
        line += f"  doi:{paper.doi}"
    if line.strip():
        t.append(line.strip() + "\n", style=f"{_DIM} italic")
    metrics = [f"score {paper.relevance_score:.2f}"]
    if paper.topical_relevance:
        metrics.append(f"topical {paper.topical_relevance:.2f}")
    if paper.venue_prestige:
        metrics.append(f"venue {paper.venue_prestige:.2f}")
    metrics.append(f"{paper.citation_count} citations")
    t.append("  ·  ".join(metrics) + "\n", style=_DIM)
    if paper.cites_author_paper:
        t.append(f'cites your paper: "{paper.cites_author_paper}"\n', style=f"{_ACCENT} italic")
    t.append(paper.url + "\n\n", style=_DIM)
    t.append(paper.abstract or "(no abstract available)", style="#b1bac4")
    return t


def _clipboard_text(paper: Paper) -> str:
    parts = [paper.title, ", ".join(paper.authors)]
    venue = " ".join(x for x in (paper.journal, paper.year()) if x)
    if venue:
        parts.append(venue)
    if paper.doi:
        parts.append(f"doi:{paper.doi}")
    parts.append(paper.url)
    return "\n".join(parts)


def resolve_cited_titles(papers: list[Paper], profile: UserProfile) -> list[Paper]:
    """Replace a raw own-paper id in ``cites_author_paper`` with its title when known."""
    titles = profile.own_paper_titles
    return [
        p.model_copy(update={"cites_author_paper": titles[p.cites_author_paper]})
        if p.cites_author_paper in titles else p
        for p in papers
    ]


class PaperItem(ListItem):
    def __init__(self, paper: Paper) -> None:
        super().__init__()
        self.paper = paper

    def compose(self) -> ComposeResult:
        yield Static(_paper_list_text(self.paper))


class ViewerApp(App[None]):
    """Read-only paper browser that follows the snapshot on disk."""

    CSS = _CSS
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit_app", "Quit"),
        Binding("r", "reload", "Reload"),
        Binding("o", "open_url", "Open"),
        Binding("d", "toggle_detail", "Detail"),
        Binding("y", "copy_paper", "Copy"),
        Binding("s", "save_paper", "Save"),
    ]

    def __init__(self, profile: UserProfile | None = None, *, follow: bool = True) -> None:
        super().__init__()
        self.profile = profile if profile is not None else load_profile()
        self.papers: list[Paper] = []
        self._note = ""
        self._fetched_at: datetime | None = None
        self._follow = follow
        self._mtime: float | None = None

    # ── Composition ───────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Static(Text(_ASCII_BANNER, style=_ACCENT, justify="center"), id="banner")
        yield Static("", id="subtitle")
        yield ListView(id="papers")
        yield Static(id="detail")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "HIGOR"
        self.query_one(ListView).focus()
        self.run_worker(self._load(), name="load")
        if self._follow:
            self.set_interval(POLL_SECONDS, self._poll)

    # ── Loading ───────────────────────────────────────────────────────────────

    def _snapshot_mtime(self) -> float | None:
        path = paths.last_session_path()
        return path.stat().st_mtime if path.exists() else None

    async def _poll(self) -> None:
        if self._snapshot_mtime() != self._mtime:
            await self._load()

    async def _load(self) -> None:
        self._mtime = self._snapshot_mtime()
        loaded = store.load_last_session()
        if loaded is None:
            self._note = "no list yet — ask your agent for a digest"
            await self._rebuild([])
            return
        papers, fetched_at = loaded
        self._fetched_at = fetched_at
        await self._rebuild(resolve_cited_titles(papers, self.profile))

    async def _rebuild(self, papers: list[Paper]) -> None:
        lv = self.query_one(ListView)
        keep = lv.index
        self.papers = papers
        await lv.clear()
        for p in papers:
            await lv.append(PaperItem(p))
        if papers:
            lv.index = min(keep or 0, len(papers) - 1)
        self._refresh_subtitle()
        self._refresh_detail()

    def _refresh_subtitle(self) -> None:
        bits = [f"{len(self.papers)} papers"]
        if self.profile.name:
            bits.append(self.profile.name)
        if self._note:
            bits.append(self._note)
        elif self._fetched_at:
            age = datetime.now(tz=timezone.utc) - self._fetched_at
            bits.append(
                "updated just now" if age.total_seconds() < 120
                else f"updated {self._fetched_at:%Y-%m-%d %H:%M}"
            )
        if self._follow:
            bits.append("following")
        subtitle = "  ·  ".join(bits)
        self.sub_title = subtitle
        self.query_one("#subtitle", Static).update(Text(subtitle, style=_DIM, justify="center"))

    # ── Detail ────────────────────────────────────────────────────────────────

    def _current_paper(self) -> Paper | None:
        item = self.query_one(ListView).highlighted_child
        return item.paper if isinstance(item, PaperItem) else None

    def _refresh_detail(self) -> None:
        detail = self.query_one("#detail", Static)
        if "visible" not in detail.classes:
            return
        paper = self._current_paper()
        detail.update(_paper_detail_text(paper) if paper else Text(""))

    def on_list_view_highlighted(self, _: ListView.Highlighted) -> None:
        self._refresh_detail()

    def _log_interaction(self, action: str) -> None:
        paper = self._current_paper()
        if paper is not None:
            store.log_interaction(
                PaperInteraction(paper_id=paper.id, paper_title=paper.title, action=action)
            )

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_toggle_detail(self) -> None:
        detail = self.query_one("#detail", Static)
        if "visible" in detail.classes:
            detail.remove_class("visible")
        else:
            detail.add_class("visible")
            self._refresh_detail()
            self._log_interaction("read_full")

    def action_open_url(self) -> None:
        paper = self._current_paper()
        if paper and paper.url.startswith(("https://", "http://")):
            webbrowser.open(paper.url)
            self._log_interaction("opened")

    def action_save_paper(self) -> None:
        if self._current_paper() is not None:
            self._log_interaction("saved")
            self.notify("Saved to reading log.", timeout=2)

    def action_copy_paper(self) -> None:
        paper = self._current_paper()
        if paper is None:
            return
        text = _clipboard_text(paper)
        self.copy_to_clipboard(text)
        if shutil.which("pbcopy"):
            subprocess.run(["pbcopy"], input=text.encode(), check=False)
        self.notify("Copied title, authors and URL.", timeout=2)

    def action_reload(self) -> None:
        self.run_worker(self._load(), name="load")

    def action_quit_app(self) -> None:
        self.exit(None)


def run_viewer(follow: bool = True) -> None:
    """Launch the viewer. Returns when the researcher quits."""
    ViewerApp(follow=follow).run()
