"""Tests for the agent-level tool layer: typed `allowed_tools` enforcement
and dispatch on ConversationalAgent.

After the single-front-door collapse the only ConversationalAgent is
RevenueOpsAgent. Tool definitions are now imported as references on the
agent class, so registry-completeness is enforced by the import system
itself — no separate "is this tool registered?" test is needed.
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.registry import AGENTS_BY_SLUG
from app.agents.revenue_ops_agent import RevenueOpsAgent


# ── Agent-level least-privilege enforcement ─────────────────────────────────


@pytest.mark.asyncio
async def test_agent_rejects_unregistered_tool():
    agent = RevenueOpsAgent(
        agent_id=uuid.UUID(int=0),
        allowed_tools=list(AGENTS_BY_SLUG["revenue-ops"].allowed_tools),
    )
    with pytest.raises(PermissionError, match="fake_tool"):
        await agent.execute_tool("fake_tool", {})


@pytest.mark.asyncio
async def test_agent_allows_registered_tool():
    """Front-door agent can call an allowed tool; dispatch reaches the service."""
    agent = RevenueOpsAgent(
        agent_id=uuid.UUID(int=0),
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
        agent_id=uuid.UUID(int=0), allowed_tools=allowed
    )
    names = [s["function"]["name"] for s in agent.get_tools()]
    assert names == [t.name for t in allowed]


@pytest.mark.asyncio
async def test_agent_with_explicit_empty_allowed_tools_rejects_everything():
    """Explicitly passing `[]` overrides the class default and locks the agent down."""
    agent = RevenueOpsAgent(
        agent_id=uuid.UUID(int=0), allowed_tools=[]
    )
    assert agent.get_tools() == []
    with pytest.raises(PermissionError):
        await agent.execute_tool("get_revenue_data", {})


def test_agent_defaults_to_class_allowed_tools():
    """Omitting allowed_tools picks up the class-declared default."""
    agent = RevenueOpsAgent(agent_id=uuid.UUID(int=0))
    assert agent.allowed_tools == list(RevenueOpsAgent.allowed_tools)
    assert agent.allowed_tools, "class default should be non-empty for implemented agent"
