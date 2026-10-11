# What was extracted, changed and dropped

A record of how `higor-agent` relates to `../higor`, so the next person does not have to
diff two trees to find out.

## Carried over essentially unchanged

| From | To | Note |
| --- | --- | --- |
| `higor/search.py` | `higor_agent/search.py` | Import rename and user agent only. This is the real asset: throttling, the 429 retry ladder, cross-source de-duplication. |
| `higor/rank.py` | `higor_agent/rank.py` | The blend stays deterministic code. Week-over-week comparability depends on it. |
| `higor/store.py` | `higor_agent/store.py` | Same on-disk schema. |
| `higor/profile.py` | `higor_agent/profile.py` | Same TOML schema, same onboarding. |
| `higor/models.py` | `higor_agent/models.py` | Gained `Paper.handle`, lost `LLMError`. |
| `higor/paths.py` | `higor_agent/paths.py` | Gained the `HIGOR_DIR` environment override. |
| `higor/tui.py` | `higor_agent/view.py` | Chat pane and agent ownership removed, file following added. |

## Split apart

`higor/agent.py` was two things in one class. The paper cache, handle table, sweep and
back-fill became `session.py`. The tool bodies became `tools.py`. The `ClaudeSDKClient`
session, the streaming callbacks and the prompt assembly were **deleted**: that is what the
host harness provides.

`prompts/agent_system.md` became `agent/higor.md`. Its `{profile}`, `{history}` and
`{shown}` placeholders could not survive the move, because a static instruction file in
someone else's harness cannot be templated per run. They became the `get_context` tool,
which the instructions tell the agent to call first.

## Deliberately removed

- **`higor/claude.py` and the `claude-agent-sdk` dependency.** The package now contains no
  model call. This was the only real obstacle to running under another harness.
- **The LLM call inside `prestige.py`.** Venue rating moved to the host agent via
  `venues_needing_rating` and `set_venue_ratings`; the module is now a validated cache.
- **The in-process `on_show` callback.** `show` writes the snapshot and the viewer follows
  it, which makes the browser optional rather than load-bearing.
- **Auto-digest on launch.** The old app decided on its own that the list was stale and
  started a digest. A viewer that cannot talk to a model cannot do that; ask your harness,
  or schedule it with `digest-prompt`.

## Known trade-offs

- **The scheduled digest got worse.** `higor` was one command that produced a digest. Now
  it is a headless run of your harness, which is easy with Claude Code and awkward with
  Copilot, where there is no headless mode.
- **Nine tools instead of six.** `get_context`, `venues_needing_rating` and
  `set_venue_ratings` are the cost of not having a model in the package.
- **Two processes to run** if you want the browser.

## One thing fixed on the way

The ported `tests/test_search.py` fixtures were dated `2026-09-08`, inside a 30-day filter
that the suite asserted on. Those two tests fail on any machine running them more than a
month later, which is the state `../higor` is in today. The fixtures here are dated
relative to now.
