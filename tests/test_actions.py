import uuid

import pytest
from httpx import AsyncClient


@pytest.fixture
async def workflow_with_action(client: AsyncClient, test_agent_id: uuid.UUID) -> dict:
    wf_resp = await client.post(
        "/workflows",
        json={"kind": "sdr_outreach", "trigger_source": "manual", "initiated_by": "system"},
    )
    assert wf_resp.status_code == 201
    wf = wf_resp.json()

    act_resp = await client.post(
        f"/workflows/{wf['id']}/actions",
        json={
            "agent_id": str(test_agent_id),
            "action_type": "research",
            "summary": "Research target accounts",
            "proposed_payload": {"target": "enterprise"},
            "risk_level": "low",
        },
    )
    assert act_resp.status_code == 201
    return {"workflow": wf, "action": act_resp.json()}


@pytest.fixture
async def fresh_action(client: AsyncClient, test_agent_id: uuid.UUID) -> dict:
    wf = (
        await client.post(
            "/workflows",
            json={"kind": "sdr_outreach", "trigger_source": "manual", "initiated_by": "system"},
        )
    ).json()
    act = (
        await client.post(
            f"/workflows/{wf['id']}/actions",
            json={
                "agent_id": str(test_agent_id),
                "action_type": "send_email",
                "summary": "Send intro email",
                "proposed_payload": {"to": "ceo@example.com"},
            },
        )
    ).json()
    return act


async def test_list_actions_default_proposed(client: AsyncClient, workflow_with_action: dict):
    resp = await client.get("/actions")
    assert resp.status_code == 200
    data = resp.json()
    action_id = workflow_with_action["action"]["id"]
    assert any(a["id"] == action_id for a in data)
    statuses = {a["status"] for a in data}
    assert statuses == {"proposed"}


async def test_list_actions_status_filter(client: AsyncClient, workflow_with_action: dict):
    resp = await client.get("/actions?status=proposed")
    assert resp.status_code == 200
    statuses = {a["status"] for a in resp.json()}
    assert statuses == {"proposed"}


async def test_get_action(client: AsyncClient, workflow_with_action: dict):
    action_id = workflow_with_action["action"]["id"]
    resp = await client.get(f"/actions/{action_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == action_id
    assert data["status"] == "proposed"


async def test_get_action_not_found(client: AsyncClient):
    resp = await client.get(f"/actions/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_approve_action(client: AsyncClient, workflow_with_action: dict):
    action_id = workflow_with_action["action"]["id"]
    resp = await client.post(f"/actions/{action_id}/approve")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "completed"
    assert data["approved_by"] == "system"
    assert data["result"] == {"stub": True}
    assert data["executed_payload"] is not None


async def test_approve_action_twice_returns_409(client: AsyncClient, fresh_action: dict):
    action_id = fresh_action["id"]
    r1 = await client.post(f"/actions/{action_id}/approve")
    assert r1.status_code == 200
    r2 = await client.post(f"/actions/{action_id}/approve")
    assert r2.status_code == 409


async def test_reject_action_happy_path(client: AsyncClient, fresh_action: dict):
    action_id = fresh_action["id"]
    resp = await client.post(
        f"/actions/{action_id}/reject",
        json={"rejection_reason": "Email tone is off"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "rejected"
    assert data["rejection_reason"] == "Email tone is off"


async def test_reject_already_approved_returns_409(client: AsyncClient, fresh_action: dict):
    action_id = fresh_action["id"]
    await client.post(f"/actions/{action_id}/approve")
    resp = await client.post(
        f"/actions/{action_id}/reject",
        json={"rejection_reason": "Too late"},
    )
    assert resp.status_code == 409
