"""``higor-agent`` command line: serve, view, profile, install, digest-prompt.

There is no model here. ``serve`` exposes the tools over stdio MCP for whatever harness the
researcher uses; ``view`` is the browser; ``profile`` is onboarding. The agent itself is the
markdown in ``agent/higor.md``.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rich.console import Console

from higor_agent import paths
from higor_agent.models import HigorError

console = Console()


def setup_logging() -> None:
    """Log warnings to the state directory, creating it if this is a first run.

    ``higor_dir()`` has to come first: ``logging.basicConfig`` opens the file eagerly and
    a missing parent directory would kill the server during the host's handshake, before
    anything can report why.
    """
    paths.higor_dir()
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        filename=str(paths.log_path()),
    )


def cmd_serve(_: argparse.Namespace) -> int:
    from higor_agent.server import build_server

    build_server().run(transport="stdio")
    return 0


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
    from higor_agent.install import install, project_root, refresh_reference_copies

    dest = Path(args.dest).expanduser().resolve()
    if args.target == "reference":
        for path in refresh_reference_copies():
            console.print(f"  wrote {path}", soft_wrap=True, highlight=False)
        return 0
    for path in install(args.target, dest):
        console.print(f"  wrote {path}", soft_wrap=True, highlight=False)
    console.print("\nThe server runs as:", style="bold")
    console.print(
        f"  uv run --directory {project_root()} higor-agent serve",
        soft_wrap=True,
        highlight=False,
    )
    if args.target == "claude-code":
        console.print("Start Claude Code in that directory and ask it for your digest.")
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

    p = sub.add_parser("serve", help="run the MCP server over stdio (hosts spawn this)")
    p.set_defaults(func=cmd_serve)

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
    except HigorError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
