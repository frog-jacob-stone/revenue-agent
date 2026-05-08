"""Tests for orchestrator_v2.agent_invoke.

Stubs `call_anthropic` to avoid real network. Verifies AGENT_INVOKED /
AGENT_COMPLETED audit events bookend the call, and that AGENT_FAILED is
emitted when the underlying call raises.
"""
from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest

from app.db import get_pool
from app.orchestrator_v2 import NodeContext, agent_invoke, events


async def _seed_workflow_row(kind: str = "_agent_invoke_test"):
    """Insert a placeholder workflow row so audit events have a valid FK."""
    pool = await get_pool()
    return await pool.fetchval(
        """
        INSERT INTO workflows (kind, status, current_step, trigger_source, trigger_payload, initiated_by)
        VALUES ($1, 'running', 0, 'manual', '{}'::jsonb, 'tester')
        RETURNING id
        """,
        kind,
    )


async def _events_for(workflow_id):
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT event_type FROM audit_log WHERE workflow_id = $1 ORDER BY occurred_at, id",
        workflow_id,
    )
    return [r["event_type"] for r in rows]


async def test_invoke_agent_emits_invoked_and_completed(test_agent_slug):
    wf_id = await _seed_workflow_row()

    async def fake_call(prompt, *, model, max_tokens):
        return f"[stub from {model}]"

    # Patch the imported reference inside the agent_invoke module.
    with patch("app.orchestrator_v2.agent_invoke.call_anthropic", side_effect=fake_call):
        # Use any agent class registered in app.agents.registry. Pull a slug
        # dynamically so the test doesn't depend on a specific slug name.
        from app.agents.registry import AGENTS
        agent_cls = next(c for c in AGENTS if getattr(c, "model", ""))
        ctx = NodeContext(workflow_id=wf_id)
        result = await agent_invoke.invoke_agent(
            agent_cls.slug, {"prompt": "hi", "max_tokens": 10}, ctx,
        )

    assert result["text"].startswith("[stub")
    et = await _events_for(wf_id)
    assert events.AGENT_INVOKED in et
    assert events.AGENT_COMPLETED in et


async def test_invoke_agent_emits_failed_on_exception(test_agent_slug):
    wf_id = await _seed_workflow_row()

    async def boom(prompt, *, model, max_tokens):
        raise RuntimeError("network ded")

    from app.agents.registry import AGENTS
    agent_cls = next(c for c in AGENTS if getattr(c, "model", ""))

    with patch("app.orchestrator_v2.agent_invoke.call_anthropic", side_effect=boom):
        ctx = NodeContext(workflow_id=wf_id)
        with pytest.raises(RuntimeError, match="network ded"):
            await agent_invoke.invoke_agent(agent_cls.slug, {"prompt": "x"}, ctx)

    et = await _events_for(wf_id)
    assert events.AGENT_INVOKED in et
    assert events.AGENT_FAILED in et
    assert events.AGENT_COMPLETED not in et
