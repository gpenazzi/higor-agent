"""Generated harness wrappers: one instruction file, several front matters."""
import json
from pathlib import Path

import pytest

from higor_agent import install


def test_every_wrapper_carries_the_instruction_body():
    for rendered in (install.claude_skill(), install.agents_md()):
        assert "context`. It carries" in rendered


def test_claude_skill_declares_name_and_description():
    text = install.claude_skill()
    assert text.startswith("---\nname: higor\n")
    assert "description:" in text.splitlines()[2]


def test_claude_skill_front_matter_values_are_quoted_yaml_strings():
    # The summary contains ": ", which strict YAML parsers (Copilot CLI) reject unquoted.
    lines = install.claude_skill().splitlines()
    assert json.loads(lines[2].removeprefix("description: ")) == install.SUMMARY
    allowed = json.loads(lines[3].removeprefix("allowed-tools: "))
    assert allowed == f"Bash({install.command()}:*)"


def test_command_runs_this_checkout_from_anywhere():
    cmd = install.command(Path("/some/where"))
    assert cmd == "uv run --quiet --directory /some/where higor-agent"


def test_command_quotes_a_root_with_spaces():
    assert "'/my docs/higor'" in install.command(Path("/my docs/higor"))


def test_every_command_in_the_instructions_is_made_runnable():
    text = install.instructions(Path("/r"))
    full = "uv run --quiet --directory /r higor-agent"
    assert f"`{full} context`" in text
    assert f"   {full} show --note" in text  # inside an indented code block
    assert "`higor-agent " not in text


def test_install_claude_code_writes_the_skill_and_the_digest_command(tmp_path):
    written = install.install("claude-code", tmp_path)
    skills = tmp_path / ".claude" / "skills"
    assert written == [
        str(skills / "higor" / "SKILL.md"),
        str(skills / "higor-digest" / "SKILL.md"),
    ]
    assert install.command() in (skills / "higor" / "SKILL.md").read_text()


def test_digest_skill_is_for_the_researcher_to_invoke():
    lines = install.digest_skill().splitlines()
    assert lines[1] == "name: higor-digest"
    assert json.loads(lines[2].removeprefix("description: ")) == install.DIGEST_SUMMARY
    assert "disable-model-invocation: true" in lines
    assert f"`{install.command()} context`" in install.digest_skill()


def test_install_agents_md_writes_a_fragment(tmp_path):
    install.install("agents-md", tmp_path)
    text = (tmp_path / "AGENTS.higor.md").read_text()
    assert "agent/higor.md" in text  # points back at the source of truth


def test_an_unknown_target_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="unknown target"):
        install.install("emacs", tmp_path)


def test_checked_in_copies_are_not_stale():
    """integrations/ is generated. If this fails, run `higor-agent install reference`."""
    base = install.project_root() / "integrations"
    root = install.REFERENCE_ROOT
    expected = {
        base / "claude-code" / "skills" / "higor" / "SKILL.md": install.claude_skill(root),
        base / "claude-code" / "skills" / "higor-digest" / "SKILL.md": install.digest_skill(root),
        base / "agents-md" / "AGENTS.higor.md": install.agents_md(root),
    }
    stale = [str(path) for path, text in expected.items() if path.read_text() != text]
    assert not stale, f"regenerate these: {stale}"


def test_the_instruction_file_is_findable_from_the_package():
    from higor_agent import paths

    assert (paths.agent_dir() / "higor.md").is_file()
