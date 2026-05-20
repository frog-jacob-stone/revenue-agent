"""Tests for the shared tool layer: registry integrity, dispatch, and the
least-privilege `allowed_tools` enforcement on ConversationalAgent.

After the single-front-door collapse the only ConversationalAgent is
RevenueOpsAgent. The structural coverage (registry integrity, dispatch
routing, allow/deny enforcement) stays the same; only the targeted agent
changes.
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.registry import AGENTS_BY_SLUG
from app.agents.revenue_ops_agent import RevenueOpsAgent
from app.agents.revenue_recognition_agent import RevenueRecognitionAgent
from app.tools import TOOLS, ToolContext, execute_tool, get_tool_schemas


# RevenueRecognitionAgent is now a non-conversational BaseAgent — its tools
# live in its class attribute but it can't be instantiated. RevenueOpsAgent is
# the single ConversationalAgent that exercises the tool dispatch machinery.
IMPLEMENTED_AGENTS = (RevenueOpsAgent, RevenueRecognitionAgent)


# ── Registry integrity ──────────────────────────────────────────────────────


def test_every_allowed_tool_is_registered():
    """For each implemented agent, every entry in allowed_tools must exist in TOOLS."""
    for cls in IMPLEMENTED_AGENTS:
        for tool_name in cls.allowed_tools:
            assert tool_name in TOOLS, (
                f"Agent '{cls.slug}' lists tool '{tool_name}' but no such tool is registered"
            )


def test_tool_schemas_have_openai_shape():
    for name, tool in TOOLS.items():
        schema = tool.as_openai_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == name
        assert "parameters" in schema["function"]


def test_get_tool_schemas_filters_by_name():
    schemas = get_tool_schemas(["get_revenue_data", "bogus_tool_name"])
    names = [s["function"]["name"] for s in schemas]
    assert names == ["get_revenue_data"]


# ── Dispatch ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_tool_unknown_raises():
    ctx = ToolContext(agent_id=uuid.UUID(int=0), agent_slug="revenue-recognition", config={})
    with pytest.raises(ValueError, match="Unknown tool"):
        await execute_tool("not_a_real_tool", {}, ctx)


@pytest.mark.asyncio
async def test_execute_tool_dispatches_to_service():
    """Calling an allowed tool flows through to the service layer."""
    ctx = ToolContext(agent_id=uuid.UUID(int=0), agent_slug="revenue-recognition", config={})
    fake = {"records": []}
    with patch(
        "app.services.revenue.get_revenue_data_slim",
        new=AsyncMock(return_value=fake),
    ):
        result = await execute_tool(
            "get_revenue_data",
            {"date_from": "2026-01-01", "date_to": "2026-01-31"},
            ctx,
        )
    assert result == fake


# ── Agent-level least-privilege enforcement ─────────────────────────────────


@pytest.mark.asyncio
async def test_agent_rejects_unregistered_tool():
    agent = RevenueOpsAgent(
        agent_id=uuid.UUID(int=0),
        config={},
        allowed_tools=list(AGENTS_BY_SLUG["revenue-ops"].allowed_tools),
    )
    with pytest.raises(PermissionError, match="fake_tool"):
        await agent.execute_tool("fake_tool", {})


@pytest.mark.asyncio
async def test_agent_allows_registered_tool():
    """Front-door agent can call an allowed tool; dispatch reaches the service."""
    agent = RevenueOpsAgent(
        agent_id=uuid.UUID(int=0),
        config={},
        allowed_tools=list(AGENTS_BY_SLUG["revenue-ops"].allowed_tools),
    )
    fake = {"records": [{"id": 42}]}
    with patch(
        "app.services.revenue.get_revenue_data_slim",
        new=AsyncMock(return_value=fake),
    ):
        result = await agent.execute_tool(
            "get_revenue_data",
            {"date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
    assert result == fake


def test_agent_get_tools_matches_allowed_tools():
    allowed = list(AGENTS_BY_SLUG["revenue-ops"].allowed_tools)
    agent = RevenueOpsAgent(
        agent_id=uuid.UUID(int=0), config={}, allowed_tools=allowed
    )
    names = [s["function"]["name"] for s in agent.get_tools()]
    assert names == allowed


@pytest.mark.asyncio
async def test_agent_with_explicit_empty_allowed_tools_rejects_everything():
    """Explicitly passing `[]` overrides the class default and locks the agent down."""
    agent = RevenueOpsAgent(
        agent_id=uuid.UUID(int=0), config={}, allowed_tools=[]
    )
    assert agent.get_tools() == []
    with pytest.raises(PermissionError):
        await agent.execute_tool("get_revenue_data", {})


def test_agent_defaults_to_class_allowed_tools():
    """Omitting allowed_tools picks up the class-declared default."""
    agent = RevenueOpsAgent(agent_id=uuid.UUID(int=0), config={})
    assert agent.allowed_tools == list(RevenueOpsAgent.allowed_tools)
    assert agent.allowed_tools, "class default should be non-empty for implemented agent"
