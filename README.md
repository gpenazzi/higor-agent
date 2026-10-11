# higor-agent

Literature screening for **any** agent harness.

The agent loop lives in whatever harness you already use: Claude Code, Copilot in VS Code,
Codex, Cursor. What ships here is a tool server, a ranking core, one instruction file and a
browser.

```
your harness  ──MCP(stdio)──▶  higor-agent serve  ──HTTP──▶  Semantic Scholar · arXiv · Crossref
     │                                 │
   the agent                     writes last_session.json
  (agent/higor.md)                     │
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
3. **Blend.** The server multiplies that score by a venue term plus a citation bonus. The
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

Then wire up your harness. Each command writes the instruction file and registers the MCP
server, and nothing else in your config is touched:

```bash
uv run higor-agent install claude-code --dest ~/my-research   # .claude/skills + .mcp.json
uv run higor-agent install copilot     --dest ~/my-research   # .github/chatmodes + .vscode/mcp.json
uv run higor-agent install agents-md   --dest ~/my-research   # AGENTS fragment + .mcp.json
```

Open that directory in your harness and ask for your digest. In a second terminal:

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

| Tool | What it does |
| --- | --- |
| `get_context` | Profile, reading history, today, and what is currently shown. Call first. |
| `collect_candidates` | Sweep every configured source, paged |
| `search_papers` | One phrase on Semantic Scholar and arXiv, optionally venue-restricted |
| `related_papers` | Recommendations for, and citations of, given handles |
| `get_papers` | Full records and abstracts for up to 40 handles |
| `venues_needing_rating` | Journals with no cached prestige rating |
| `set_venue_ratings` | Cache the agent's venue judgements |
| `show` | Blend, publish to the browser, write the snapshot |
| `update_profile` | Edit the saved profile |

Every paper the agent touches gets a handle like `#12`, stable for the life of the server
process. Tools accept handles or raw ids; the browser and the snapshot show them.

## Who judges what

Every judgement comes from the host agent. Topical relevance arrives through `show`, venue
standing through `venues_needing_rating` and `set_venue_ratings`. That is why the package
needs no model of its own: it validates, caches and blends, and never decides.

A venue is rated once and the rating is reused by every later session, so the cache is worth
keeping an eye on. It is plain JSON, sorted by name, and you can correct any entry by hand.

## Requirements

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- A harness that speaks MCP
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
  higor-agent.log      warnings and errors
```

## Scheduling a weekly digest

The harness supplies the loop, so a digest is a headless run of your harness against these
tools. `higor-agent digest-prompt` prints the opening message. With Claude Code:

```bash
cd ~/my-research && claude -p "$(uv run --directory ~/Work/higor-agent higor-agent digest-prompt)"
```

## Development

```bash
uv run pytest -v
uv run higor-agent install reference   # regenerate integrations/ after editing agent/higor.md
uv run python scripts/screenshot.py    # regenerate the README images from your own list
```

`scripts/screenshot.py` draws whatever is in your state directory, so pass `--state` a copy
if you would rather not publish your own papers. It never writes to the reading log.
