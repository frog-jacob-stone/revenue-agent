"""Structural delegation allowlist enforcement (ADR-0003).

`ask_agent` reads the calling agent's `available_agents` ClassVar and refuses
any target not in that list. The LLM cannot escape the constraint by
hallucinating an unauthorized slug — the tool returns `Blocked`.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

# Import agent_invoke so `app.orchestrator.agent_invoke` is loaded and
# patchable below (it's lazy-imported inside `_ask_agent`).
import app.orchestrator.agent_invoke  # noqa: F401
from app.agents.tools import ToolContext
from app.agents.tools.agent.ask_agent import _ask_agent
from app.agents.tools.base import Blocked


async def test_ask_agent_blocks_unauthorized_target():
    """A domain agent (BDR) cannot delegate to revenue-ops — its
    `available_agents` is empty by default."""
    ctx = ToolContext(agent_id=uuid.UUID(int=0), agent_slug="bdr")

    result = await _ask_agent(
        ctx,
        target_slug="revenue-ops",
        prompt="Explain the January revenue dip.",
    )

    assert isinstance(result, Blocked)
    assert "bdr" in result.reason
    assert "revenue-ops" in result.reason


async def test_ask_agent_blocks_unknown_caller():
    """An agent_slug not in the registry is rejected."""
    ctx = ToolContext(agent_id=uuid.UUID(int=0), agent_slug="ghost-agent")

    result = await _ask_agent(
        ctx,
        target_slug="bdr",
        prompt="Anything.",
    )

    assert isinstance(result, Blocked)
    assert "ghost-agent" in result.reason


async def test_ask_agent_permits_orchestrator_to_call_domain_agent():
    """The chief-of-staff declares `available_agents` covering all three
    domain agents. Calls to any of them pass the allowlist check and proceed."""
    ctx = ToolContext(agent_id=uuid.UUID(int=0), agent_slug="chief-of-staff")

    fake_response = {"text": "answer from bdr"}
    with patch(
        "app.orchestrator.agent_invoke.run_agent_task",
        new=AsyncMock(return_value=fake_response),
    ):
        result = await _ask_agent(
            ctx,
            target_slug="bdr",
            prompt="Draft a reply for lead@example.com.",
        )

    # Allowlist passed → not Blocked. The tool returned a Done payload.
    assert not isinstance(result, Blocked)
    assert result.payload["answer"] == "answer from bdr"
