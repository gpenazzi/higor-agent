"""``higor-agent`` command line: the tools, plus view, profile, install, digest-prompt.

There is no model here. The tool commands (context, candidates, search, related, papers,
venues, rate-venues, show, update-profile) are what the host agent runs from its shell,
one process per call, each printing plain text for the model to read. ``view`` is the
browser; ``profile`` is onboarding. The agent itself is the markdown in ``agent/higor.md``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from rich.console import Console

from higor_agent import paths
from higor_agent.models import HigorError

console = Console()
errors = Console(stderr=True)


def setup_logging() -> None:
    """Log warnings to the state directory, creating it if this is a first run.

    ``higor_dir()`` has to come first: ``logging.basicConfig`` opens the file eagerly and
    a missing parent directory would kill the first command, before anything can report why.
    """
    paths.higor_dir()
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        filename=str(paths.log_path()),
    )


# ── Tool commands ─────────────────────────────────────────────────────────────

ToolCall = Callable[[Any, argparse.Namespace], Coroutine[Any, Any, str]]


def cmd_tool(args: argparse.Namespace) -> int:
    """Run one tool against the saved session and print its text for the agent."""
    from higor_agent.tools import Toolkit
    from higor_agent.workspace import open_session

    with open_session() as session:
        text = asyncio.run(args.call(Toolkit(session), args))
    sys.stdout.write(text.rstrip() + "\n")
    return 0


def read_json(source: str) -> Any:
    """JSON from a file, or from stdin when *source* is '-'."""
    text = sys.stdin.read() if source == "-" else Path(source).expanduser().read_text()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc


async def call_show(kit: Any, args: argparse.Namespace) -> str:
    from higor_agent.tools import Judgement

    data = read_json(args.source)
    entries = data.get("papers", []) if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError("expected a JSON list of {ref, relevance, summary} objects")
    papers = [Judgement.model_validate(e) for e in entries]
    return await kit.show(papers, note=args.note, mode="append" if args.append else "replace")


async def call_rate_venues(kit: Any, args: argparse.Namespace) -> str:
    ratings = read_json(args.source)
    if not isinstance(ratings, dict):
        raise ValueError('expected a JSON object such as {"Physical Review B": 0.7}')
    return await kit.set_venue_ratings(ratings)


async def call_update_profile(kit: Any, args: argparse.Namespace) -> str:
    return await kit.update_profile(
        research_interests=args.research_interests,
        add_primary_topics=args.add_primary_topic,
        remove_primary_topics=args.remove_primary_topic,
        add_secondary_topics=args.add_secondary_topic,
        remove_secondary_topics=args.remove_secondary_topic,
        add_journals=args.add_journal,
        remove_journals=args.remove_journal,
        add_arxiv_categories=args.add_arxiv_category,
        remove_arxiv_categories=args.remove_arxiv_category,
        set_fields_of_study=args.set_fields_of_study,
        set_impact_weight=args.set_impact_weight,
        set_lookback_days=args.set_lookback_days,
        set_max_papers=args.set_max_papers,
    )


def add_tool_parsers(sub: argparse._SubParsersAction) -> None:
    def tool(name: str, help: str, call: ToolCall) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help, description=help)
        p.set_defaults(func=cmd_tool, call=call)
        return p

    tool(
        "context",
        "profile, reading history, today's date and the list currently shown; run first",
        lambda kit, a: kit.get_context(),
    )

    p = tool(
        "candidates",
        "sweep every source in the profile; one row per paper, paged",
        lambda kit, a: kit.collect_candidates(days=a.days, page=a.page),
    )
    p.add_argument("--days", type=int, help="lookback window; default from the profile")
    p.add_argument("--page", type=int, default=1, help="page 2, 3… reuses the same sweep")

    p = tool(
        "search",
        "one exact-phrase query on Semantic Scholar and arXiv",
        lambda kit, a: kit.search_papers(a.query, days=a.days, venues=a.venue),
    )
    p.add_argument("query")
    p.add_argument("--days", type=int, help="lookback window; default from the profile")
    p.add_argument("--venue", action="append", help="restrict to a venue (repeatable)")

    p = tool(
        "related",
        "recommendations for, and papers citing, the given handles",
        lambda kit, a: kit.related_papers(a.refs, days=a.days),
    )
    p.add_argument("refs", nargs="+", help="handles such as #12, or raw ids")
    p.add_argument("--days", type=int, help="only papers from the last N days")

    p = tool(
        "papers",
        "full records and abstracts for up to 40 handles",
        lambda kit, a: kit.get_papers(a.refs),
    )
    p.add_argument("refs", nargs="+", help="handles such as #12, or raw ids")

    p = tool(
        "venues",
        "journals with no cached prestige rating, among the handles or the last sweep",
        lambda kit, a: kit.venues_needing_rating(a.refs),
    )
    p.add_argument("refs", nargs="*", help="handles; default: the last sweep")

    p = tool(
        "rate-venues",
        'cache venue ratings given as a JSON object {"journal": 0-1}',
        call_rate_venues,
    )
    p.add_argument("source", nargs="?", default="-", help="JSON file, or - for stdin (default)")

    p = tool(
        "show",
        "blend and publish a ranked list given as JSON [{ref, relevance, summary}, ...]",
        call_show,
    )
    p.add_argument("source", nargs="?", default="-", help="JSON file, or - for stdin (default)")
    p.add_argument("--note", default="", help="a line shown above the list")
    p.add_argument("--append", action="store_true", help="add to the current list")

    p = tool(
        "update-profile",
        "edit the saved profile; list options add or remove one value and repeat",
        call_update_profile,
    )
    p.add_argument("--research-interests")
    for field in ("primary-topic", "secondary-topic", "journal", "arxiv-category"):
        p.add_argument(f"--add-{field}", action="append")
        p.add_argument(f"--remove-{field}", action="append")
    p.add_argument("--set-fields-of-study", nargs="+")
    p.add_argument("--set-impact-weight", type=float)
    p.add_argument("--set-lookback-days", type=int)
    p.add_argument("--set-max-papers", type=int)


# ── Everything else ───────────────────────────────────────────────────────────


def cmd_view(args: argparse.Namespace) -> int:
    from higor_agent.view import run_viewer

    if not paths.profile_path().exists():
        console.print(
            "[yellow]No profile yet.[/yellow] Run [bold]higor-agent profile[/bold] first."
        )
        return 1
    run_viewer(follow=not args.no_follow)
    return 0


def cmd_profile(args: argparse.Namespace) -> int:
    from higor_agent.profile import describe_profile, load_profile, run_onboarding

    if args.show:
        if not paths.profile_path().exists():
            console.print("[yellow]No profile yet.[/yellow]")
            return 1
        console.print(describe_profile(load_profile()))
        return 0
    existing = load_profile() if paths.profile_path().exists() else None
    run_onboarding(existing=existing)
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    from higor_agent.install import command, install, project_root, refresh_reference_copies

    dest = Path(args.dest).expanduser().resolve()
    if args.target == "reference":
        for path in refresh_reference_copies():
            console.print(f"  wrote {path}", soft_wrap=True, highlight=False)
        return 0
    for path in install(args.target, dest):
        console.print(f"  wrote {path}", soft_wrap=True, highlight=False)
    console.print("\nThe agent runs the tools as:", style="bold")
    console.print(f"  {command(project_root())} <command>", soft_wrap=True, highlight=False)
    if args.target == "claude-code":
        console.print(
            "Start Claude Code or Copilot CLI in that directory and ask for your digest."
        )
    console.print("Run [bold]higor-agent view[/bold] in a second terminal to watch the list.")
    return 0


def cmd_digest_prompt(args: argparse.Namespace) -> int:
    """Print the opening message for a digest, for piping into a headless harness."""
    from higor_agent.profile import load_profile

    profile = load_profile()
    days = args.days or profile.lookback_days
    console.print(
        f"Build my digest for the last {days} days. Collect the candidates, read the titles, "
        f"read the abstracts of the promising ones, rate any unrated venues, and show the best "
        f"{profile.max_papers_per_week} papers with scores and summaries. Then give me a short "
        "overview of the week.",
        highlight=False,
        soft_wrap=True,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="higor-agent",
        description="Literature-screening tools for any agent harness.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    add_tool_parsers(sub)

    p = sub.add_parser("view", help="terminal paper browser following the published list")
    p.add_argument("--no-follow", action="store_true", help="do not watch the file for changes")
    p.set_defaults(func=cmd_view)

    p = sub.add_parser("profile", help="create or edit the research profile")
    p.add_argument("--show", action="store_true", help="print the saved profile and exit")
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser("install", help="write the agent files for a harness")
    p.add_argument(
        "target",
        choices=["claude-code", "agents-md", "reference"],
        help="which harness to wire up ('reference' regenerates integrations/)",
    )
    p.add_argument("--dest", default=".", help="directory to write into (default: here)")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("digest-prompt", help="print the digest opening message for a harness")
    p.add_argument("--days", type=int, default=0, help="lookback window; default from profile")
    p.set_defaults(func=cmd_digest_prompt)

    return parser


def main() -> None:
    setup_logging()
    args = build_parser().parse_args()
    try:
        sys.exit(args.func(args))
    except (HigorError, ValueError) as exc:
        errors.print(f"Error: {exc}", markup=False, highlight=False)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
