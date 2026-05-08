"""Tests for app.orchestrator_v2.runner.

Each test registers a tiny graph against the shared singleton runner, drives
it, then unregisters so the registry is fresh for the next test.
"""
from __future__ import annotations

from typing import NotRequired, TypedDict
from uuid import UUID, uuid4

import pytest
from langgraph.graph import END, StateGraph

from app.db import get_pool
from app.orchestrator_v2 import GraphSpec, events, runner


class TinyState(TypedDict, total=False):
    workflow_id: NotRequired[str]
    counter: int
    failed_at: NotRequired[str]
    boom: NotRequired[bool]
    _propose: NotRequired[dict]


@pytest.fixture
def kind() -> str:
    return f"_runner_test_{uuid4().hex[:8]}"


async def _events_for(workflow_id: UUID) -> list[str]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT event_type FROM audit_log WHERE workflow_id = $1 ORDER BY occurred_at, id",
        workflow_id,
    )
    return [r["event_type"] for r in rows]


# ── Two-node auto graph runs to completion ──────────────────────────────────────


def _two_node_graph_factory():
    def n1(state: TinyState) -> TinyState:
        return {"counter": state.get("counter", 0) + 1}

    def n2(state: TinyState) -> TinyState:
        return {"counter": state.get("counter", 0) + 10}

    g: StateGraph = StateGraph(TinyState)
    g.add_node("n1", n1)
    g.add_node("n2", n2)
    g.set_entry_point("n1")
    g.add_edge("n1", "n2")
    g.add_edge("n2", END)
    return GraphSpec(graph=g)


async def test_runner_drives_two_node_graph_to_completion(kind: str):
    runner.register(kind, _two_node_graph_factory)
    try:
        wf_id = await runner.start(kind, initial_state={"counter": 0})
    finally:
        runner.unregister(kind)

    pool = await get_pool()
    wf = await pool.fetchrow("SELECT status FROM workflows WHERE id = $1", wf_id)
    assert wf["status"] == "completed"

    et = await _events_for(wf_id)
    assert events.WORKFLOW_STARTED in et
    assert events.NODE_EXITED in et
    assert et.count(events.NODE_EXITED) == 2
    assert events.WORKFLOW_COMPLETED in et


# ── Failing node propagates to workflow.failed ──────────────────────────────────


def _exploding_graph_factory():
    def n1(state: TinyState) -> TinyState:
        return {"counter": 1}

    def n2(state: TinyState) -> TinyState:
        raise RuntimeError("boom")

    g: StateGraph = StateGraph(TinyState)
    g.add_node("n1", n1)
    g.add_node("n2", n2)
    g.set_entry_point("n1")
    g.add_edge("n1", "n2")
    g.add_edge("n2", END)
    return GraphSpec(graph=g)


async def test_runner_marks_workflow_failed_on_node_exception(kind: str):
    runner.register(kind, _exploding_graph_factory)
    try:
        wf_id = await runner.start(kind, initial_state={"counter": 0})
    finally:
        runner.unregister(kind)

    pool = await get_pool()
    wf = await pool.fetchrow("SELECT status FROM workflows WHERE id = $1", wf_id)
    assert wf["status"] == "failed"

    et = await _events_for(wf_id)
    assert events.WORKFLOW_FAILED in et
    assert events.NODE_FAILED in et


# ── Interrupt-before pauses, writes approvals row, sets awaiting_approval ───────


def _propose_graph_factory():
    def n1(state: TinyState) -> TinyState:
        return {
            "counter": (state.get("counter", 0) + 1),
            "_propose": {
                "action_type": "other",
                "agent_slug": "test-agent",
                "summary": "please approve",
                "proposed_payload": {"hello": "world"},
                "node_name": "n2",
            },
        }

    def n2(state: TinyState) -> TinyState:
        return {"counter": state.get("counter", 0) + 100}

    g: StateGraph = StateGraph(TinyState)
    g.add_node("n1", n1)
    g.add_node("n2", n2)
    g.set_entry_point("n1")
    g.add_edge("n1", "n2")
    g.add_edge("n2", END)
    return GraphSpec(graph=g, interrupt_before=("n2",))


async def test_runner_pauses_at_interrupt_and_writes_approval(kind: str, test_agent_slug):
    runner.register(kind, _propose_graph_factory)
    try:
        wf_id = await runner.start(kind, initial_state={"counter": 0})
    finally:
        # Don't unregister yet — resume tests reuse via a separate fixture.
        runner.unregister(kind)

    pool = await get_pool()
    wf = await pool.fetchrow("SELECT status FROM workflows WHERE id = $1", wf_id)
    assert wf["status"] == "awaiting_approval"

    rows = await pool.fetch(
        "SELECT * FROM approvals WHERE workflow_id = $1", wf_id
    )
    assert len(rows) == 1
    appr = rows[0]
    assert appr["status"] == "pending"
    assert appr["action_type"] == "other"
    assert appr["proposed_payload"] == {"hello": "world"}

    et = await _events_for(wf_id)
    assert events.WORKFLOW_PAUSED in et
    assert events.APPROVAL_REQUESTED in et
