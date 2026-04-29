"""
Tests for the shared tool layer: registry integrity, dispatch, and the
least-privilege `allowed_tools` enforcement on ConversationalAgent.
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.invoice_analytics import InvoiceAnalyticsAgent
from app.agents.invoice_operations import InvoiceOperationsAgent
from app.agents.registry import AGENTS_BY_SLUG
from app.agents.revenue_recognition import RevenueRecognitionAgent
from app.tools import TOOLS, ToolContext, execute_tool, get_tool_schemas


IMPLEMENTED_AGENTS = (
    InvoiceOperationsAgent,
    InvoiceAnalyticsAgent,
    RevenueRecognitionAgent,
)


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
    schemas = get_tool_schemas(["list_harvest_clients", "bogus_tool_name"])
    names = [s["function"]["name"] for s in schemas]
    assert names == ["list_harvest_clients"]


# ── Dispatch ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_tool_unknown_raises():
    ctx = ToolContext(agent_id=uuid.UUID(int=0), agent_slug="invoice-analytics", config={})
    with pytest.raises(ValueError, match="Unknown tool"):
        await execute_tool("not_a_real_tool", {}, ctx)


@pytest.mark.asyncio
async def test_execute_tool_dispatches_to_service():
    """Calling an allowed tool flows through to the service layer."""
    ctx = ToolContext(agent_id=uuid.UUID(int=0), agent_slug="invoice-analytics", config={})
    fake = [{"id": 1, "name": "Acme Corp"}]
    with patch(
        "app.services.clients.list_active_clients",
        new=AsyncMock(return_value=fake),
    ):
        result = await execute_tool("list_harvest_clients", {}, ctx)
    assert result == fake


# ── Agent-level least-privilege enforcement ─────────────────────────────────


@pytest.mark.asyncio
async def test_agent_rejects_disallowed_tool():
    """Analytics agent must not be able to invoke invoice generation."""
    agent = InvoiceAnalyticsAgent(
        agent_id=uuid.UUID(int=0),
        config={},
        allowed_tools=list(AGENTS_BY_SLUG["invoice-analytics"].allowed_tools),
    )
    with pytest.raises(PermissionError, match="trigger_invoice_generation"):
        await agent.execute_tool(
            "trigger_invoice_generation",
            {"client_id": 1, "period_start": "2026-01-01", "period_end": "2026-01-31"},
        )


@pytest.mark.asyncio
async def test_agent_rejects_unregistered_tool():
    agent = InvoiceAnalyticsAgent(
        agent_id=uuid.UUID(int=0),
        config={},
        allowed_tools=list(AGENTS_BY_SLUG["invoice-analytics"].allowed_tools),
    )
    with pytest.raises(PermissionError, match="fake_tool"):
        await agent.execute_tool("fake_tool", {})


@pytest.mark.asyncio
async def test_agent_allows_registered_tool():
    """Analytics agent can call an allowed tool; it reaches the service."""
    agent = InvoiceAnalyticsAgent(
        agent_id=uuid.UUID(int=0),
        config={},
        allowed_tools=list(AGENTS_BY_SLUG["invoice-analytics"].allowed_tools),
    )
    with patch(
        "app.services.clients.list_active_clients",
        new=AsyncMock(return_value=[{"id": 42, "name": "Beta Inc"}]),
    ):
        result = await agent.execute_tool("list_harvest_clients", {})
    assert result == [{"id": 42, "name": "Beta Inc"}]


def test_agent_get_tools_matches_allowed_tools():
    allowed = list(AGENTS_BY_SLUG["invoice-operations"].allowed_tools)
    agent = InvoiceOperationsAgent(
        agent_id=uuid.UUID(int=0), config={}, allowed_tools=allowed
    )
    names = [s["function"]["name"] for s in agent.get_tools()]
    assert names == allowed


def test_agent_get_tools_for_revenue_recognition():
    allowed = list(AGENTS_BY_SLUG["revenue-recognition"].allowed_tools)
    agent = RevenueRecognitionAgent(
        agent_id=uuid.UUID(int=0), config={}, allowed_tools=allowed
    )
    names = [s["function"]["name"] for s in agent.get_tools()]
    assert names == allowed


@pytest.mark.asyncio
async def test_agent_with_explicit_empty_allowed_tools_rejects_everything():
    """Explicitly passing `[]` overrides the class default and locks the agent down."""
    agent = InvoiceAnalyticsAgent(
        agent_id=uuid.UUID(int=0), config={}, allowed_tools=[]
    )
    assert agent.get_tools() == []
    with pytest.raises(PermissionError):
        await agent.execute_tool("list_harvest_clients", {})


def test_agent_defaults_to_class_allowed_tools():
    """Omitting allowed_tools picks up the class-declared default."""
    agent = InvoiceAnalyticsAgent(agent_id=uuid.UUID(int=0), config={})
    assert agent.allowed_tools == list(InvoiceAnalyticsAgent.allowed_tools)
    assert agent.allowed_tools, "class default should be non-empty for implemented agent"
