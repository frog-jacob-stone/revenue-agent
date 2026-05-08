"""Tests for orchestrator_v2.spawn.

Verifies a sub-workflow gets a parent_workflow_id linkage and SUBWORKFLOW_SPAWNED
fires on the parent's audit trail.
"""
from __future__ import annotations

from typing import NotRequired, TypedDict
from uuid import uuid4

import pytest
from langgraph.graph import END, StateGraph

from app.db import get_pool
from app.orchestrator_v2 import GraphSpec, events, runner, spawn_workflow


class TrivialState(TypedDict, total=False):
    done: NotRequired[bool]


@pytest.fixture
def child_kind() -> str:
    return f"_spawn_child_{uuid4().hex[:8]}"


def _trivial_factory():
    def n(state: TrivialState) -> TrivialState:
        return {"done": True}

    g: StateGraph = StateGraph(TrivialState)
    g.add_node("n", n)
    g.set_entry_point("n")
    g.add_edge("n", END)
    return GraphSpec(graph=g)


async def _seed_parent_workflow():
    pool = await get_pool()
    return await pool.fetchval(
        """
        INSERT INTO workflows (kind, status, current_step, trigger_source, trigger_payload, initiated_by)
        VALUES ('_parent', 'running', 0, 'manual', '{}'::jsonb, 'tester')
        RETURNING id
        """,
    )


async def test_spawn_workflow_links_parent_and_emits_event(child_kind: str):
    parent_id = await _seed_parent_workflow()
    runner.register(child_kind, _trivial_factory)
    try:
        child_id = await spawn_workflow(
            child_kind,
            initial_state={"done": False},
            parent_workflow_id=parent_id,
        )
    finally:
        runner.unregister(child_kind)

    pool = await get_pool()
    child_wf = await pool.fetchrow(
        "SELECT id, kind, status, parent_workflow_id FROM workflows WHERE id = $1",
        child_id,
    )
    assert child_wf is not None
    assert child_wf["parent_workflow_id"] == parent_id
    assert child_wf["status"] == "completed"

    parent_events = await pool.fetch(
        "SELECT event_type FROM audit_log WHERE workflow_id = $1 ORDER BY occurred_at, id",
        parent_id,
    )
    assert events.SUBWORKFLOW_SPAWNED in [r["event_type"] for r in parent_events]
