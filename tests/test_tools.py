"""Tests for the agent-level tool layer: typed `allowed_tools` enforcement
and dispatch on the Agent class.

After the agent-class collapse (ADR-0003) the only orchestrator is
ChiefOfStaffAgent. Tool definitions are imported as references on the
agent class, so registry-completeness is enforced by the import system
itself — no separate "is this tool registered?" test is needed.

These tests target the domain `RevenueOpsAgent` because it owns
`get_revenue_data` (the tool exercised by `test_agent_allows_registered_tool`).

The per-instance `allowed_tools` override was removed; tool allowlists are
class-level structural properties only.
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.revenue_ops_agent import RevenueOpsAgent

# ── Agent-level least-privilege enforcement ─────────────────────────────────


@pytest.mark.asyncio
async def test_agent_rejects_unregistered_tool():
    agent = RevenueOpsAgent(agent_id=uuid.UUID(int=0))
    with pytest.raises(PermissionError, match="fake_tool"):
        await agent.execute_tool("fake_tool", {})


@pytest.mark.asyncio
async def test_agent_allows_registered_tool():
    """Orchestrator can call an allowed tool; dispatch reaches the service."""
    agent = RevenueOpsAgent(agent_id=uuid.UUID(int=0))
    fake_records = [{"id": 42}]
    with patch(
        "app.services.revenue.get_revenue_data_slim",
        new=AsyncMock(return_value=fake_records),
    ):
        result = await agent.execute_tool(
            "get_revenue_data",
            {"date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
    # Tool now wraps the service's list output as {"records": [...]} (ADR-0002).
    assert result == {"records": fake_records}


def test_agent_get_tools_matches_class_allowed_tools():
    agent = RevenueOpsAgent(agent_id=uuid.UUID(int=0))
    names = [s["function"]["name"] for s in agent.get_tools()]
    assert names == [t.name for t in RevenueOpsAgent.allowed_tools]


def test_agent_uses_class_allowed_tools():
    """Instance exposes the class-declared allowed_tools."""
    agent = RevenueOpsAgent(agent_id=uuid.UUID(int=0))
    assert agent.allowed_tools is RevenueOpsAgent.allowed_tools
    assert agent.allowed_tools, "class default should be non-empty for implemented agent"
