"""Tests for app/services/agent_messages.

Pure DB-layer tests — no LangGraph checkpointer, no LLM calls. Run inside
the existing per-test rollback fixture, so each test sees a clean table.
"""
from __future__ import annotations

from uuid import uuid4

from app.db import get_pool
from app.services import agent_messages


async def _seed_workflow_row(kind: str = "_agent_messages_test"):
    pool = await get_pool()
    return await pool.fetchval(
        """
        INSERT INTO workflows
            (kind, status, current_step, trigger_source, trigger_payload, initiated_by)
        VALUES ($1, 'running', 0, 'manual', '{}'::jsonb, 'tester')
        RETURNING id
        """,
        kind,
    )


async def test_send_message_with_new_thread():
    pool = await get_pool()
    row = await agent_messages.send_message(
        pool,
        from_agent_slug="agent-a",
        to_agent_slug="agent-b",
        content="hello",
    )

    assert row["thread_id"] is not None
    assert row["from_agent_slug"] == "agent-a"
    assert row["to_agent_slug"] == "agent-b"
    assert row["content"] == "hello"
    assert row["workflow_id"] is None

    rows = await pool.fetch(
        "SELECT * FROM agent_messages WHERE thread_id = $1",
        row["thread_id"],
    )
    assert len(rows) == 1


async def test_read_thread_returns_messages_in_order():
    pool = await get_pool()
    thread_id = uuid4()

    await agent_messages.send_message(
        pool, from_agent_slug="a", to_agent_slug="b",
        content="one", thread_id=thread_id,
    )
    await agent_messages.send_message(
        pool, from_agent_slug="b", to_agent_slug="a",
        content="two", thread_id=thread_id,
    )
    await agent_messages.send_message(
        pool, from_agent_slug="a", to_agent_slug="b",
        content="three", thread_id=thread_id,
    )

    messages = await agent_messages.read_thread(pool, thread_id)
    assert [m["content"] for m in messages] == ["one", "two", "three"]


async def test_get_messages_for_workflow_filters_correctly():
    pool = await get_pool()
    wf_a = await _seed_workflow_row("workflow-a")
    wf_b = await _seed_workflow_row("workflow-b")

    # Two messages for workflow A (different threads), one for B, one with no workflow.
    await agent_messages.send_message(
        pool, from_agent_slug="a", to_agent_slug="b",
        content="A1", workflow_id=wf_a,
    )
    await agent_messages.send_message(
        pool, from_agent_slug="b", to_agent_slug="a",
        content="A2", workflow_id=wf_a,
    )
    await agent_messages.send_message(
        pool, from_agent_slug="a", to_agent_slug="b",
        content="B1", workflow_id=wf_b,
    )
    await agent_messages.send_message(
        pool, from_agent_slug="a", to_agent_slug="b",
        content="chat-no-workflow",
    )

    a_messages = await agent_messages.get_messages_for_workflow(pool, wf_a)
    assert {m["content"] for m in a_messages} == {"A1", "A2"}

    b_messages = await agent_messages.get_messages_for_workflow(pool, wf_b)
    assert [m["content"] for m in b_messages] == ["B1"]
