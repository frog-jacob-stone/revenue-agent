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


async def test_propose_action(client: AsyncClient, workflow: dict, test_agent_id: uuid.UUID):
    resp = await client.post(
        f"/workflows/{workflow['id']}/actions",
        json={
            "agent_id": str(test_agent_id),
            "action_type": "research",
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


async def test_propose_action_auto_sequence(client: AsyncClient, workflow: dict, test_agent_id: uuid.UUID):
    payload = {
        "agent_id": str(test_agent_id),
        "action_type": "research",
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


async def test_get_workflow_includes_actions(client: AsyncClient, workflow: dict, test_agent_id: uuid.UUID):
    await client.post(
        f"/workflows/{workflow['id']}/actions",
        json={
            "agent_id": str(test_agent_id),
            "action_type": "research",
            "summary": "Gather info",
            "proposed_payload": {},
        },
    )
    resp = await client.get(f"/workflows/{workflow['id']}")
    assert resp.status_code == 200
    assert len(resp.json()["actions"]) >= 1
