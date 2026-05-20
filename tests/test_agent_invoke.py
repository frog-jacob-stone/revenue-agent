"""Tests for orchestrator.agent_invoke.

Scopes a `FakeProvider` via `use_provider()` so no real OpenAI traffic happens.
Verifies AGENT_INVOKED / AGENT_COMPLETED audit events bookend the call, and
that AGENT_FAILED is emitted when the underlying call raises.
"""
from __future__ import annotations

import pytest

from app.db import get_pool
from app.integrations.llm import LlmResponse, use_provider
from app.orchestrator import NodeContext, agent_invoke, events
from tests.fakes.llm import FakeProvider


async def _seed_workflow_row(kind: str = "_agent_invoke_test"):
    """Insert a placeholder workflow row so audit events have a valid FK."""
    pool = await get_pool()
    return await pool.fetchval(
        """
        INSERT INTO workflows (kind, status, trigger_source, trigger_payload, initiated_by)
        VALUES ($1, 'running', 'manual', '{}'::jsonb, 'tester')
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
    fake = FakeProvider(
        completions=[
            LlmResponse(text="[stub from model]", finish_reason="stop")
        ]
    )

    with use_provider(fake):
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
    fake = FakeProvider(completions=[RuntimeError("network ded")])

    from app.agents.registry import AGENTS
    agent_cls = next(c for c in AGENTS if getattr(c, "model", ""))

    with use_provider(fake):
        ctx = NodeContext(workflow_id=wf_id)
        with pytest.raises(RuntimeError, match="network ded"):
            await agent_invoke.invoke_agent(agent_cls.slug, {"prompt": "x"}, ctx)

    et = await _events_for(wf_id)
    assert events.AGENT_INVOKED in et
    assert events.AGENT_FAILED in et
    assert events.AGENT_COMPLETED not in et
