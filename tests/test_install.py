"""Generated harness wrappers: one instruction file, several front matters."""
import json

import pytest

from higor_agent import install


def test_every_wrapper_carries_the_instruction_body():
    body = install.instructions()
    assert "get_context" in body
    for rendered in (install.claude_skill(), install.agents_md()):
        assert "Call `get_context`" in rendered


def test_claude_skill_declares_name_and_description():
    text = install.claude_skill()
    assert text.startswith("---\nname: higor\n")
    assert "description:" in text.splitlines()[2]


def test_claude_skill_description_is_a_quoted_yaml_string():
    # The summary contains ": ", which strict YAML parsers (Copilot CLI) reject unquoted.
    value = install.claude_skill().splitlines()[2].removeprefix("description: ")
    assert json.loads(value) == install.SUMMARY


def test_server_command_runs_this_checkout():
    command = install.server_command()
    assert command["command"] == "uv"
    assert command["args"][:2] == ["run", "--directory"]
    assert command["args"][-2:] == ["higor-agent", "serve"]


def test_mcp_config_uses_the_key_the_host_expects():
    assert "mcpServers" in install.mcp_config("mcpServers")
    assert install.mcp_config("mcpServers")["mcpServers"]["higor"]["type"] == "stdio"


def test_install_claude_code_writes_skill_and_config(tmp_path):
    written = install.install("claude-code", tmp_path)
    skill = tmp_path / ".claude" / "skills" / "higor" / "SKILL.md"
    config = tmp_path / ".mcp.json"
    assert skill.exists() and config.exists()
    assert str(skill) in written
    assert json.loads(config.read_text())["mcpServers"]["higor"]["command"] == "uv"


def test_install_agents_md_writes_a_fragment(tmp_path):
    install.install("agents-md", tmp_path)
    text = (tmp_path / "AGENTS.higor.md").read_text()
    assert "agent/higor.md" in text  # points back at the source of truth


def test_merging_preserves_other_servers(tmp_path):
    config = tmp_path / ".mcp.json"
    config.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "keep": 1}))
    install.install("claude-code", tmp_path)
    doc = json.loads(config.read_text())
    assert doc["mcpServers"]["other"] == {"command": "x"}
    assert doc["keep"] == 1
    assert "higor" in doc["mcpServers"]


def test_reinstalling_replaces_only_the_higor_entry(tmp_path):
    install.install("claude-code", tmp_path)
    install.install("claude-code", tmp_path)
    doc = json.loads((tmp_path / ".mcp.json").read_text())
    assert list(doc["mcpServers"]) == ["higor"]


def test_an_unparseable_config_is_left_alone(tmp_path):
    config = tmp_path / ".mcp.json"
    config.write_text("{ not json")
    written = install.install("claude-code", tmp_path)
    assert config.read_text() == "{ not json"
    assert any("left alone" in line for line in written)


def test_an_unknown_target_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="unknown target"):
        install.install("emacs", tmp_path)


def test_checked_in_copies_are_not_stale():
    """integrations/ is generated. If this fails, run `higor-agent install reference`."""
    base = install.project_root() / "integrations"
    expected = {
        base / "claude-code" / "skills" / "higor" / "SKILL.md": install.claude_skill(),
        base / "agents-md" / "AGENTS.higor.md": install.agents_md(),
    }
    stale = [str(path) for path, text in expected.items() if path.read_text() != text]
    assert not stale, f"regenerate these: {stale}"


def test_the_instruction_file_is_findable_from_the_package():
    from higor_agent import paths

    assert (paths.agent_dir() / "higor.md").is_file()
