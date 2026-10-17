# higor-agent — LLM context file

## What this is

Literature screening for any agent harness that can run shell commands. Extracted from
`../higor`, which was a terminal app owning its own Claude session. Here the harness owns the loop; this
project owns the papers, the ranking and the browser.

**The package contains no model call and no provider SDK. Keep it that way** — it is the
whole point of the split. Anything that needs a judgement asks the host agent for it
through a tool.

## Architecture

```
agent/higor.md    THE agent: instructions, rubric, digest procedure. Single source of truth.
higor_agent/
  tools.py        the nine tools as plain async functions over a Session, returning text
  session.py      paper cache, handle table, profile-driven sweep, back-fill, apply_show
  workspace.py    saves the Session between commands (session_cache.json) under a lock
  search.py       s2_search / s2_citations_of / s2_recommendations / s2_fetch /
                  arxiv_search / crossref_fill / merge_papers
  rank.py         is_preprint, citation_norm, blend_score, score_papers
  prestige.py     venue cache only: unrated / set_ratings / prestige_for. No LLM.
  profile.py      load/save/describe profile, interactive onboarding
  store.py        reading log (jsonl) + last-session snapshot
  view.py         Textual viewer; follows last_session.json, writes the reading log
  install.py      renders harness wrappers from agent/higor.md, with the full command path
  cli.py          one subcommand per tool, plus view / profile / install / digest-prompt
  paths.py        every on-disk location (tests monkeypatch HIGOR_DIR)
  models.py       UserProfile, Paper, PaperInteraction, HigorError/SearchError/ProfileError
integrations/     generated reference copies of the wrappers; regenerate, never hand-edit
```

## The two process boundaries

- **Harness to commands**: the shell. The agent runs `higor-agent <command>`, one process
  per call, and reads its stdout. Handles and the paper cache persist in
  `session_cache.json` between calls, dropped after `SESSION_TTL` idle; a file lock
  serialises commands so they never race on the cache or on the S2 rate limit.
- **Commands to viewer**: `last_session.json`. `show` writes it, the viewer polls its mtime
  every two seconds. The viewer is optional; never make the tools depend on it.

## Ranking model

- The agent judges **topical relevance only** (rubric in `agent/higor.md`), after reading
  abstracts via `papers`.
- `show` blends it: `final = topical · (1 + impact_weight · ((prestige − 0.5)·2 + citation_bonus))`.
- Prestige comes from the cache. Unrated venues and preprints are neutral (0.5), so a
  missing rating never penalises a paper. Citations are a bonus only.
- The agent fills the cache via `venues` then `rate-venues`. `show`
  reports how many venues fell back to neutral so the agent can fix it and re-show.
- Citing the user's work is displayed (`cites_author_paper`) but is not a score floor.

## Conventions

- Python ≥ 3.11, all I/O async (`httpx.AsyncClient`).
- **Always use `uv`**: `uv sync`, `uv run higor-agent`, `uv run pytest -v`. Never bare
  pip/python/pytest.
- Tool bodies live in `tools.py` and know nothing of argument parsing, so tests call them
  directly. `cli.main` prints `HigorError` and `ValueError` as one line on stderr with exit
  code 1, which is what the agent reads back; tool output goes to stdout as plain text,
  never through Rich (it would eat `[brackets]` as markup).
- Agent instructions live in `agent/higor.md`, never inline and never duplicated per
  harness. Write commands there as `higor-agent <command>`; rendering substitutes the
  runnable `uv run --directory <checkout>` form. After editing it run
  `higor-agent install reference`; a test fails if the checked-in copies drift.
- Errors: `HigorError` → `SearchError` / `ProfileError`.
- `logging`, not `print`. The CLI logs to `$HIGOR_DIR/higor-agent.log`. Rich and Textual
  are display only.
- S2 requests are serialised and throttled (`S2_MIN_INTERVAL`); `S2_API_KEY` lifts it.
  The instructions tell the agent to use the commands, not its own web requests: the
  commands are what serialise them.
- Tests must never touch the real state directory: `tests/conftest.py` redirects
  `higor_agent.paths.HIGOR_DIR`. Any new on-disk location goes through `paths.py`.
- Test fixtures must be dated relative to now. A hard-coded date ages out of a "last N
  days" filter and the suite starts failing on a calendar rather than on a change.

## Storage layout

Defaults to `~/.higor`, shared with the original Higor (same schemas). `HIGOR_DIR`
overrides it.

```
profile.toml         user profile (incl. [sources] and impact_weight)
journal_ranks.json   cached venue prestige scores (hand-editable)
reading_log.jsonl    append-only interaction log (opened / read_full / saved)
last_session.json    the published list; the viewer follows this file
session_cache.json   papers and handles between commands; session.lock guards it
higor-agent.log      warnings and errors
```

## Out of scope

- Any model call, provider SDK or agent loop inside this package
- Server-side storage; notifications
- Learning across runs beyond feeding the reading log into `context`
- Any GUI beyond the terminal viewer
