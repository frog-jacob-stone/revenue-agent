import uuid

import pytest
from httpx import AsyncClient


@pytest.fixture
async def workflow(client: AsyncClient, test_agent_id: uuid.UUID) -> dict:
    resp = await client.post(
        "/workflows",
        json={
            "kind": "sdr_outreach",
            "trigger_source": "manual",
            "subject_type": "company",
            "subject_id": "acme-001",
            "subject_ref": {"name": "Acme Corp"},
            "initiated_by": "system",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_create_workflow(client: AsyncClient, test_agent_id: uuid.UUID):
    resp = await client.post(
        "/workflows",
        json={
            "kind": "proposal_generation",
            "trigger_source": "manual",
            "initiated_by": "system",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["kind"] == "proposal_generation"
    assert data["status"] == "running"
    assert uuid.UUID(data["id"])


async def test_get_workflow(client: AsyncClient, workflow: dict):
    resp = await client.get(f"/workflows/{workflow['id']}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == workflow["id"]
    assert data["kind"] == workflow["kind"]
    assert isinstance(data["actions"], list)


async def test_get_workflow_not_found(client: AsyncClient):
    resp = await client.get(f"/workflows/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_list_workflows(client: AsyncClient, workflow: dict):
    resp = await client.get("/workflows")
    assert resp.status_code == 200
    ids = [w["id"] for w in resp.json()]
    assert workflow["id"] in ids


async def test_list_workflows_status_filter(client: AsyncClient, workflow: dict):
    resp = await client.get("/workflows?status=running")
    assert resp.status_code == 200
    statuses = {w["status"] for w in resp.json()}
    assert statuses == {"running"}


async def test_list_workflows_kind_filter(client: AsyncClient, workflow: dict):
    resp = await client.get(f"/workflows?kind={workflow['kind']}")
    assert resp.status_code == 200
    data = resp.json()
    assert any(w["id"] == workflow["id"] for w in data)


async def test_propose_action(client: AsyncClient, workflow: dict, test_agent_slug: str):
    resp = await client.post(
        f"/workflows/{workflow['id']}/actions",
        json={
            "agent_slug": test_agent_slug,
            "action_type": "other",
            "summary": "Research Acme Corp contacts",
            "proposed_payload": {"query": "Acme Corp decision makers"},
            "reasoning": "Need to identify the right contacts before outreach",
            "risk_level": "low",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["status"] == "proposed"
    assert data["sequence"] == 1
    assert data["workflow_id"] == workflow["id"]


async def test_propose_action_auto_sequence(client: AsyncClient, workflow: dict, test_agent_slug: str):
    payload = {
        "agent_slug": test_agent_slug,
        "action_type": "other",
        "summary": "Step N",
        "proposed_payload": {},
    }
    r1 = await client.post(f"/workflows/{workflow['id']}/actions", json=payload)
    r2 = await client.post(f"/workflows/{workflow['id']}/actions", json=payload)
    assert r1.status_code == 201
    assert r2.status_code == 201
    seq1 = r1.json()["sequence"]
    seq2 = r2.json()["sequence"]
    assert seq2 == seq1 + 1


async def test_get_workflow_includes_actions(client: AsyncClient, workflow: dict, test_agent_slug: str):
    await client.post(
        f"/workflows/{workflow['id']}/actions",
        json={
            "agent_slug": test_agent_slug,
            "action_type": "other",
            "summary": "Gather info",
            "proposed_payload": {},
        },
    )
    resp = await client.get(f"/workflows/{workflow['id']}")
    assert resp.status_code == 200
    assert len(resp.json()["actions"]) >= 1


async def test_get_workflow_trace_404(client: AsyncClient):
    resp = await client.get(f"/workflows/{uuid.uuid4()}/trace")
    assert resp.status_code == 404


async def test_get_workflow_trace_includes_internal_steps(
    client: AsyncClient, test_agent_slug: str
):
    """Trace endpoint returns ALL actions — including auto-progressed
    task/llm_step rows that the inbox filter excludes."""
    from app.db import get_pool
    from app.models.workflows import WorkflowPattern
    from app.orchestrator import (
        Chain,
        CheckpointStep,
        LLMStep,
        TaskStep,
        orchestrator,
        register_chain,
    )
    from app.orchestrator.chain import _reset_registry_for_tests

    _reset_registry_for_tests()
    try:
        def const(value):
            async def handler(ctx):
                return value
            return handler

        register_chain(Chain(
            kind="test_trace",
            pattern=WorkflowPattern.prompt_chain_action,
            agent_slug=test_agent_slug,
            steps=(
                TaskStep("Pull data", const({"x": 1})),
                LLMStep("Summarize", const({"x": 2})),
                CheckpointStep("Approve"),
            ),
        ))
        workflow_id = await orchestrator.start("test_trace")
    finally:
        _reset_registry_for_tests()

    resp = await client.get(f"/workflows/{workflow_id}/trace")
    assert resp.status_code == 200
    body = resp.json()

    assert body["workflow_id"] == str(workflow_id)
    assert body["kind"] == "test_trace"
    assert body["pattern"] == "prompt_chain_action"
    assert body["status"] == "awaiting_approval"
    assert body["current_step"] == 2

    actions = body["actions"]
    assert len(actions) == 3
    assert [a["step_kind"] for a in actions] == ["task", "llm_step", "checkpoint"]
    assert [a["sequence"] for a in actions] == [1, 2, 3]
    assert [a["attempt_number"] for a in actions] == [1, 1, 1]

    # Auto-progressed steps have a duration; the still-pending checkpoint does not.
    assert actions[0]["duration_ms"] is not None and actions[0]["duration_ms"] >= 0
    assert actions[1]["duration_ms"] is not None
    assert actions[2]["duration_ms"] is None


async def test_get_workflow_trace_preserves_retry_relationships(
    client: AsyncClient, test_agent_slug: str
):
    """Retry actions point back via retry_of_action_id."""
    from app.models.workflows import WorkflowPattern
    from app.orchestrator import (
        Chain,
        CritiqueStep,
        LLMStep,
        orchestrator,
        register_chain,
    )
    from app.orchestrator.chain import _reset_registry_for_tests

    _reset_registry_for_tests()
    try:
        critique_calls = {"n": 0}

        async def draft(ctx):
            return {"draft": "x"}

        async def critique(ctx):
            critique_calls["n"] += 1
            return {"passed": critique_calls["n"] >= 2, "score": 0.5, "feedback": "", "issues": []}

        register_chain(Chain(
            kind="test_trace_retry",
            pattern=WorkflowPattern.prompt_chain_action,
            agent_slug=test_agent_slug,
            steps=(
                LLMStep("Draft", draft),
                CritiqueStep("Check", critique, critiques_step_index=0, max_attempts=3),
            ),
        ))
        workflow_id = await orchestrator.start("test_trace_retry")
    finally:
        _reset_registry_for_tests()

    body = (await client.get(f"/workflows/{workflow_id}/trace")).json()
    actions = body["actions"]
    assert len(actions) == 4  # draft1, critique1, draft2, critique2
    # Retry rows point back to their first attempts.
    assert actions[2]["retry_of_action_id"] == actions[0]["id"]
    assert actions[3]["retry_of_action_id"] == actions[1]["id"]
    # First attempts have no retry_of.
    assert actions[0]["retry_of_action_id"] is None
    assert actions[1]["retry_of_action_id"] is None
    # Critique results are preserved on the row.
    assert actions[1]["critique_result"]["passed"] is False
    assert actions[3]["critique_result"]["passed"] is True
