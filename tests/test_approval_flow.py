"""End-to-end approval flow: pause → approve → resume → complete (and reject path)."""
from __future__ import annotations

import json
from typing import NotRequired, TypedDict
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from langgraph.graph import END, StateGraph

from app.db import get_pool
from app.orchestrator import GraphSpec, runner


class FlowState(TypedDict, total=False):
    counter: int
    finished: NotRequired[bool]
    executed_payload: NotRequired[dict]
    _propose: NotRequired[dict]


@pytest.fixture
def kind() -> str:
    return f"_flow_test_{uuid4().hex[:8]}"


def _flow_factory():
    def n1(state: FlowState) -> FlowState:
        return {
            "counter": state.get("counter", 0) + 1,
            "_propose": {
                "action_type": "other",
                "agent_slug": "test-agent",
                "summary": "approve me",
                "proposed_payload": {"x": 1},
                "node_name": "n2",
            },
        }

    def n2(state: FlowState) -> FlowState:
        # Read the human-edited (or original) payload back out of state.
        # The runner pushes executed_payload onto state on resume.
        ep = state.get("executed_payload") or {}
        return {"finished": True, "counter": state.get("counter", 0) + ep.get("x", 0)}

    g: StateGraph = StateGraph(FlowState)
    g.add_node("n1", n1)
    g.add_node("n2", n2)
    g.set_entry_point("n1")
    g.add_edge("n1", "n2")
    g.add_edge("n2", END)
    return GraphSpec(graph=g, interrupt_before=("n2",))


async def test_approve_resumes_to_completion(client: AsyncClient, kind: str, test_agent_slug):
    runner.register(kind, _flow_factory)
    try:
        wf_id = await runner.start(kind, initial_state={"counter": 0})

        pool = await get_pool()
        appr = await pool.fetchrow(
            "SELECT id FROM approvals WHERE workflow_id = $1", wf_id
        )
        assert appr is not None

        resp = await client.post(
            f"/approvals/{appr['id']}/approve",
            json={"approved_by": "tester"},
        )
        assert resp.status_code == 200, resp.text

        # Resume runs in BackgroundTasks; tests need to drive it explicitly.
        await runner.resume(wf_id)

        wf = await pool.fetchrow("SELECT status FROM workflows WHERE id = $1", wf_id)
        assert wf["status"] == "completed"
        appr_after = await pool.fetchrow(
            "SELECT status FROM approvals WHERE id = $1", appr["id"]
        )
        assert appr_after["status"] == "executed"
    finally:
        runner.unregister(kind)


async def test_approve_with_executed_payload_override(
    client: AsyncClient, kind: str, test_agent_slug,
):
    runner.register(kind, _flow_factory)
    try:
        wf_id = await runner.start(kind, initial_state={"counter": 0})
        pool = await get_pool()
        appr = await pool.fetchrow(
            "SELECT id FROM approvals WHERE workflow_id = $1", wf_id
        )
        # Human edits the payload before approving — x bumped from 1 to 99.
        resp = await client.post(
            f"/approvals/{appr['id']}/approve",
            json={"approved_by": "tester", "executed_payload": {"x": 99}},
        )
        assert resp.status_code == 200
        await runner.resume(wf_id)

        appr_final = await pool.fetchrow(
            "SELECT status, executed_payload FROM approvals WHERE id = $1", appr["id"]
        )
        assert appr_final["status"] == "executed"
        ep = appr_final["executed_payload"]
        if isinstance(ep, str):
            ep = json.loads(ep)
        assert ep == {"x": 99}
    finally:
        runner.unregister(kind)


async def test_reject_fails_workflow_without_running_gated_node(
    client: AsyncClient, kind: str, test_agent_slug,
):
    runner.register(kind, _flow_factory)
    try:
        wf_id = await runner.start(kind, initial_state={"counter": 0})
        pool = await get_pool()
        appr = await pool.fetchrow(
            "SELECT id FROM approvals WHERE workflow_id = $1", wf_id
        )
        resp = await client.post(
            f"/approvals/{appr['id']}/reject",
            json={"rejected_by": "tester", "rejection_reason": "no thanks"},
        )
        assert resp.status_code == 200, resp.text

        await runner.resume(wf_id)

        wf = await pool.fetchrow("SELECT status FROM workflows WHERE id = $1", wf_id)
        assert wf["status"] == "failed"
        appr_after = await pool.fetchrow(
            "SELECT status, rejection_reason FROM approvals WHERE id = $1", appr["id"]
        )
        assert appr_after["status"] == "rejected"
        assert appr_after["rejection_reason"] == "no thanks"
    finally:
        runner.unregister(kind)
