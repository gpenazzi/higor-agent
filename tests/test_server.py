"""The MCP seam: registration, schemas, and how failures reach the host."""
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from higor_agent.models import SearchError
from higor_agent.server import INSTRUCTIONS, build_server
from higor_agent.session import Session
from higor_agent.tools import Toolkit
from tests.conftest import make_paper, make_profile

EXPECTED_TOOLS = {
    "get_context",
    "collect_candidates",
    "search_papers",
    "related_papers",
    "get_papers",
    "venues_needing_rating",
    "set_venue_ratings",
    "show",
    "update_profile",
}


@pytest.fixture
def server(monkeypatch):
    async def no_fill(self, papers):
        return None

    monkeypatch.setattr(Session, "fill_details", no_fill)
    session = Session(profile=make_profile(), shown=[])
    session.register(make_paper(1, journal="Physical Review B"))
    return build_server(Toolkit(session))


async def test_every_tool_is_registered(server):
    assert {t.name for t in await server.list_tools()} == EXPECTED_TOOLS


async def test_every_tool_is_described_for_the_model(server):
    for tool in await server.list_tools():
        assert tool.description and len(tool.description) > 40, tool.name


async def test_server_instructions_point_at_get_context():
    assert "get_context" in INSTRUCTIONS


async def test_show_schema_requires_handle_score_and_summary(server):
    show = next(t for t in await server.list_tools() if t.name == "show")
    judgement = show.input_schema["$defs"]["Judgement"]
    assert set(judgement["required"]) == {"ref", "relevance", "summary"}
    assert judgement["properties"]["relevance"]["maximum"] == 1.0


async def test_read_only_tools_are_annotated(server):
    by_name = {t.name for t in await server.list_tools() if t.annotations and t.annotations.read_only_hint}
    assert "collect_candidates" in by_name
    assert "show" not in by_name


async def test_a_successful_call_returns_plain_text(server):
    result = await server.call_tool("get_context", {})
    assert result.is_error is False
    assert "RESEARCHER PROFILE" in result.content[0].text


async def test_an_expected_failure_keeps_its_message(server):
    with pytest.raises(ToolError, match="query is required"):
        await server.call_tool("search_papers", {"query": ""})


async def test_a_source_outage_reaches_the_model_as_text(server, monkeypatch):
    async def boom(*a, **k):
        raise SearchError("Semantic Scholar request failed: timeout")

    monkeypatch.setattr(Session, "sweep", boom)
    with pytest.raises(ToolError, match="Semantic Scholar request failed"):
        await server.call_tool("collect_candidates", {})


async def test_bad_arguments_are_rejected_by_the_schema(server):
    with pytest.raises(ToolError):
        await server.call_tool("show", {"papers": [{"ref": "#1", "relevance": 5, "summary": "x"}]})


async def test_the_venue_rating_round_trip_works_over_the_seam(server):
    pending = await server.call_tool("venues_needing_rating", {"refs": ["#1"]})
    assert "Physical Review B" in pending.content[0].text
    stored = await server.call_tool(
        "set_venue_ratings", {"ratings": {"Physical Review B": 0.9}}
    )
    assert "Cached 1 venue ratings" in stored.content[0].text
    again = await server.call_tool("venues_needing_rating", {"refs": ["#1"]})
    assert "No unrated venues" in again.content[0].text
