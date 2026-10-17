# higor-agent

Literature screening for **any** agent harness.

The agent loop lives in whatever harness you already use: Claude Code, Copilot CLI,
Codex, Cursor. What ships here is a set of shell commands, a ranking core, one instruction
file and a browser.

```
your harness  ──shell──▶  higor-agent <command>  ──HTTP──▶  Semantic Scholar · arXiv · Crossref
     │                            │
   the agent              writes last_session.json
  (agent/higor.md)                │
                           higor-agent view   ← you read papers here
```

The package contains **no model and no provider SDK**. Your harness is the only thing that
talks to an LLM, which is what makes the same workflow portable.

![The higor-agent viewer showing a ranked digest of ten papers](docs/screenshot.png)

## What it does

1. **Collect.** One sweep per digest: your topic phrases on Semantic Scholar (exact-phrase,
   restricted to your fields of study and a server-side date window), the same phrases on
   arXiv within your categories, every recent paper from the journals you follow, papers
   that cite your own work, and Semantic Scholar recommendations seeded by your papers.
   A few hundred candidates, de-duplicated across sources.
2. **Read.** The agent reads every title, fetches abstracts for the promising ones (missing
   abstracts are back-filled from Semantic Scholar's TL;DR, then Crossref) and scores
   topical relevance.
3. **Blend.** `show` multiplies that score by a venue term plus a citation bonus. The
   agent rates each new journal once and the rating is cached in `journal_ranks.json`;
   preprints are neutral. `impact_weight` in the profile controls how much this moves things.
4. **Browse.** `show` writes the ranked list to disk and the viewer redraws. Keyboard
   actions there are the only source of the reading log, which feeds back into later runs.

## Install

```bash
cd higor-agent
uv sync
uv run higor-agent profile          # build ~/.higor/profile.toml
```

Then give your harness the instructions. Each command writes one file and touches nothing
else; the agent runs the tools from its shell, so there is no server to register:

```bash
uv run higor-agent install claude-code --dest ~/my-research   # .claude/skills/higor{,-digest}
uv run higor-agent install agents-md   --dest ~/my-research   # AGENTS.higor.md fragment
```

GitHub Copilot CLI reads `.claude/skills/` too, so use the `claude-code` target for it.
Copilot only loads a directory's skills once you trust it, so accept the trust prompt the
first time you run `copilot` there.

Open that directory in your harness and type `/higor-digest`, or `/higor-digest 30` for a
longer window. Plain requests work too ("more like #12", "search for GPU tight binding"). In a second terminal:

```bash
uv run higor-agent view
```

The viewer follows the file, so the list updates as the agent publishes it.

Press `d` on any paper for the abstract and the scoring signals behind its rank.

![The abstract pane open on a paper, showing its final, topical and venue scores](docs/screenshot-detail.png)

### Keys

| Key | Action |
| --- | --- |
| `o` | Open in the browser, logged as read |
| `d` | Toggle the abstract pane, logged as read |
| `y` | Copy title, authors and URL |
| `s` | Mark as saved in the reading log |
| `r` | Reload the snapshot now |
| `q` | Quit |

## The tools

Each tool is a `higor-agent` subcommand that prints plain text for the agent to read.

| Command | What it does |
| --- | --- |
| `context` | Profile, reading history, today, and what is currently shown. Run first. |
| `candidates` | Sweep every configured source, paged with `--page` |
| `search` | One phrase on Semantic Scholar and arXiv, optionally venue-restricted |
| `related` | Recommendations for, and citations of, given handles |
| `papers` | Full records and abstracts for up to 40 handles |
| `venues` | Journals with no cached prestige rating |
| `rate-venues` | Cache the agent's venue judgements (JSON on stdin) |
| `show` | Blend, publish to the browser, write the snapshot (JSON on stdin) |
| `update-profile` | Edit the saved profile |

Every paper the agent touches gets a handle like `#12`. Commands accept handles or raw ids;
the browser and the snapshot show them. Handles live in `session_cache.json` between
commands and start over after twelve idle hours, so each day's conversation begins at `#1`.
A lock makes concurrent commands wait their turn, which also keeps them inside the
Semantic Scholar rate limit.

## Who judges what

Every judgement comes from the host agent. Topical relevance arrives through `show`, venue
standing through `venues` and `rate-venues`. That is why the package
needs no model of its own: it validates, caches and blends, and never decides.

A venue is rated once and the rating is reused by every later session, so the cache is worth
keeping an eye on. It is plain JSON, sorted by name, and you can correct any entry by hand.

## Requirements

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- A harness that can run shell commands
- Optional: a free [Semantic Scholar API key](https://www.semanticscholar.org/product/api)
  in `S2_API_KEY` lifts the unauthenticated rate limit

## State

Everything lives in `~/.higor` by default. Set `HIGOR_DIR` to use another tree.

```
~/.higor/
  profile.toml         your profile, including [sources] and impact_weight
  journal_ranks.json   cached venue prestige, hand-editable
  reading_log.jsonl    append-only interaction log
  last_session.json    the published list; the viewer follows this
  session_cache.json   papers and handles between commands
  higor-agent.log      warnings and errors
```

## Scheduling a weekly digest

The harness supplies the loop, so a digest is a headless run of your harness against these
tools, started with the same slash command. With Claude Code:

```bash
cd ~/my-research && claude -p "/higor-digest"
```

With Copilot CLI (`--allow-all-tools` is required in non-interactive mode):

```bash
cd ~/my-research && copilot --allow-all-tools -p "/higor-digest"
```

## Development

```bash
uv run pytest -v
uv run higor-agent install reference   # regenerate integrations/ after editing agent/higor.md
uv run python scripts/screenshot.py    # regenerate the README images from your own list
```

`scripts/screenshot.py` draws whatever is in your state directory, so pass `--state` a copy
if you would rather not publish your own papers. It never writes to the reading log.
