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


async def test_get_workflow_trace_404(client: AsyncClient):
    resp = await client.get(f"/workflows/{uuid.uuid4()}/trace")
    assert resp.status_code == 404


async def test_get_workflow_trace_returns_events(
    client: AsyncClient, workflow: dict
):
    resp = await client.get(f"/workflows/{workflow['id']}/trace")
    assert resp.status_code == 200
    body = resp.json()
    assert body["workflow_id"] == workflow["id"]
    assert body["kind"] == workflow["kind"]
    assert isinstance(body["events"], list)
