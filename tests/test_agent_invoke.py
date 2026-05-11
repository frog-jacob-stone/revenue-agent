"""Tests for orchestrator.agent_invoke.

Stubs `call_openai_chat` to avoid real network. Verifies AGENT_INVOKED /
AGENT_COMPLETED audit events bookend the call, and that AGENT_FAILED is
emitted when the underlying call raises.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.db import get_pool
from app.orchestrator import NodeContext, agent_invoke, events


def _fake_completion(text: str) -> SimpleNamespace:
    """Build the minimal shape of a ChatCompletion used by invoke_agent."""
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=text, tool_calls=None, role="assistant"),
            finish_reason="stop",
        )],
        usage=SimpleNamespace(
            prompt_tokens=1, completion_tokens=1, total_tokens=2,
            model_dump=lambda: {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        ),
    )


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

    async def fake_call(**kwargs):
        return _fake_completion(f"[stub from {kwargs.get('model')}]")

    with patch("app.orchestrator.agent_invoke.call_openai_chat", side_effect=fake_call):
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

    async def boom(**kwargs):
        raise RuntimeError("network ded")

    from app.agents.registry import AGENTS
    agent_cls = next(c for c in AGENTS if getattr(c, "model", ""))

    with patch("app.orchestrator.agent_invoke.call_openai_chat", side_effect=boom):
        ctx = NodeContext(workflow_id=wf_id)
        with pytest.raises(RuntimeError, match="network ded"):
            await agent_invoke.invoke_agent(agent_cls.slug, {"prompt": "x"}, ctx)

    et = await _events_for(wf_id)
    assert events.AGENT_INVOKED in et
    assert events.AGENT_FAILED in et
    assert events.AGENT_COMPLETED not in et
