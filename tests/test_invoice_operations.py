"""
Tests for the Invoice Operations agent.

IMPORTANT: No test in this file may call a real Harvest API write endpoint.
This is a production Harvest account. All harvest.create_invoice_draft,
harvest.send_invoice, and harvest.delete_invoice calls MUST be mocked.
End-to-end validation (chat → approval → invoice created in Harvest) is
performed manually by the developer.
"""
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


# ── Shared Harvest fixture data ───────────────────────────────────────────────

FAKE_CLIENT = {
    "id": 9001,
    "name": "Acme Corp",
    "payment_term": "net30",
}

FAKE_PROJECT_TM = {
    "id": 1001,
    "name": "Acme Platform",
    "bill_by": "Tasks",
    "budget": None,
    "client": {"id": 9001, "name": "Acme Corp"},
}

FAKE_PROJECT_FF = {
    "id": 1002,
    "name": "Acme Fixed Engagement",
    "bill_by": "none",
    "budget": 25000.0,
    "client": {"id": 9001, "name": "Acme Corp"},
}

FAKE_TIME_ENTRIES = [
    {
        "id": 201,
        "spent_date": "2026-04-10",
        "hours": 8.0,
        "billable": True,
        "billable_rate": 175.0,
        "task": {"id": 11, "name": "Software Development"},
        "project": {"id": 1001, "name": "Acme Platform"},
        "client": {"id": 9001, "name": "Acme Corp"},
        "notes": "",
    },
    {
        "id": 202,
        "spent_date": "2026-04-11",
        "hours": 4.0,
        "billable": True,
        "billable_rate": 175.0,
        "task": {"id": 11, "name": "Software Development"},
        "project": {"id": 1001, "name": "Acme Platform"},
        "client": {"id": 9001, "name": "Acme Corp"},
        "notes": "",
    },
    {
        "id": 203,
        "spent_date": "2026-04-12",
        "hours": 3.0,
        "billable": True,
        "billable_rate": 150.0,
        "task": {"id": 12, "name": "Project Management"},
        "project": {"id": 1001, "name": "Acme Platform"},
        "client": {"id": 9001, "name": "Acme Corp"},
        "notes": "",
    },
]

FAKE_INVOICE_RESULT = {
    "id": 55001,
    "number": "INV-2026-001",
    "state": "draft",
    "client": {"id": 9001, "name": "Acme Corp"},
    "amount": 2625.0,
    "due_amount": 2625.0,
    "issue_date": "2026-04-30",
    "due_date": "2026-05-30",
}


# ── Agent run() — proposal shape ─────────────────────────────────────────────

@pytest.fixture
def agent(test_agent_id):
    from app.agents.invoice_operations import InvoiceOperationsAgent
    return InvoiceOperationsAgent(agent_id=test_agent_id, config={})


@pytest.mark.asyncio
async def test_tm_proposal_line_items(agent):
    """T&M project: line items grouped by task, amounts correct."""
    with (
        patch("app.agents.invoice_operations.harvest.get_client", new=AsyncMock(return_value=FAKE_CLIENT)),
        patch("app.agents.invoice_operations.harvest.get_project", new=AsyncMock(return_value=FAKE_PROJECT_TM)),
        patch("app.agents.invoice_operations.harvest.get_time_entries_for_period", new=AsyncMock(return_value=FAKE_TIME_ENTRIES)),
    ):
        proposals = await agent.run(
            workflow_id=uuid.uuid4(),
            context={
                "client_id": 9001,
                "period_start": "2026-04-01",
                "period_end": "2026-04-30",
                "project_id": 1001,
            },
        )

    assert len(proposals) == 1
    p = proposals[0]
    assert p["action_type"] == "generate_invoice"
    assert p["risk_level"] == "medium"

    payload = p["proposed_payload"]
    assert payload["client_name"] == "Acme Corp"
    assert payload["payment_term"] == "net30"
    assert payload["due_date"] == "2026-05-30"

    line_items = payload["line_items"]
    assert len(line_items) == 2

    dev_line = next(li for li in line_items if "Software Development" in li["description"])
    assert dev_line["quantity"] == 12.0  # 8 + 4 hours
    assert dev_line["unit_price"] == 175.0
    assert dev_line["amount"] == 2100.0
    assert dev_line["_billing_model"] == "time_and_materials"

    pm_line = next(li for li in line_items if "Project Management" in li["description"])
    assert pm_line["quantity"] == 3.0
    assert pm_line["unit_price"] == 150.0
    assert pm_line["amount"] == 450.0

    assert payload["subtotal"] == 2550.0


@pytest.mark.asyncio
async def test_fixed_fee_proposal_single_line_item(agent):
    """Fixed fee project: single line item with budget amount."""
    with (
        patch("app.agents.invoice_operations.harvest.get_client", new=AsyncMock(return_value=FAKE_CLIENT)),
        patch("app.agents.invoice_operations.harvest.get_project", new=AsyncMock(return_value=FAKE_PROJECT_FF)),
    ):
        proposals = await agent.run(
            workflow_id=uuid.uuid4(),
            context={
                "client_id": 9001,
                "period_start": "2026-04-01",
                "period_end": "2026-04-30",
                "project_id": 1002,
            },
        )

    assert len(proposals) == 1
    payload = proposals[0]["proposed_payload"]
    assert len(payload["line_items"]) == 1
    li = payload["line_items"][0]
    assert li["quantity"] == 1
    assert li["unit_price"] == 25000.0
    assert li["amount"] == 25000.0
    assert li["_billing_model"] == "fixed_fee"
    assert payload["subtotal"] == 25000.0


@pytest.mark.asyncio
async def test_no_billable_entries_raises(agent):
    """No billable entries in period raises ValueError (not a silent empty invoice)."""
    empty_entries: list[Any] = []
    with (
        patch("app.agents.invoice_operations.harvest.get_client", new=AsyncMock(return_value=FAKE_CLIENT)),
        patch("app.agents.invoice_operations.harvest.get_active_projects", new=AsyncMock(return_value=[FAKE_PROJECT_TM])),
        patch("app.agents.invoice_operations.harvest.get_time_entries_for_period", new=AsyncMock(return_value=empty_entries)),
    ):
        with pytest.raises(ValueError, match="No billable line items"):
            await agent.run(
                workflow_id=uuid.uuid4(),
                context={
                    "client_id": 9001,
                    "period_start": "2026-04-01",
                    "period_end": "2026-04-30",
                },
            )


@pytest.mark.asyncio
async def test_missing_context_raises(agent):
    """Missing required context fields raise ValueError."""
    with pytest.raises(ValueError, match="requires client_id"):
        await agent.run(workflow_id=uuid.uuid4(), context={})


@pytest.mark.asyncio
async def test_sweep_mode_raises_not_implemented(agent):
    """Monthly sweep is scaffolded but disabled in v1."""
    with pytest.raises(NotImplementedError, match="not active in v1"):
        await agent.run(workflow_id=uuid.uuid4(), context={"sweep_mode": True})


@pytest.mark.asyncio
async def test_harvest_payload_strips_private_fields(agent):
    """The harvest_payload sent to Harvest must not contain _ prefixed display fields."""
    with (
        patch("app.agents.invoice_operations.harvest.get_client", new=AsyncMock(return_value=FAKE_CLIENT)),
        patch("app.agents.invoice_operations.harvest.get_active_projects", new=AsyncMock(return_value=[FAKE_PROJECT_TM])),
        patch("app.agents.invoice_operations.harvest.get_time_entries_for_period", new=AsyncMock(return_value=FAKE_TIME_ENTRIES)),
    ):
        proposals = await agent.run(
            workflow_id=uuid.uuid4(),
            context={"client_id": 9001, "period_start": "2026-04-01", "period_end": "2026-04-30"},
        )

    harvest_payload = proposals[0]["proposed_payload"]["harvest_payload"]
    for li in harvest_payload["line_items"]:
        assert not any(k.startswith("_") for k in li), f"Private field leaked into harvest_payload: {li}"


# ── Execution dispatcher — generate_invoice ───────────────────────────────────

@pytest.mark.asyncio
async def test_execution_generate_invoice_calls_harvest(client: AsyncClient, test_agent_slug: str):
    """Approving a generate_invoice action calls harvest.create_invoice_draft (mocked)."""
    wf = (
        await client.post(
            "/workflows",
            json={"kind": "invoice_generation", "trigger_source": "manual", "initiated_by": "system"},
        )
    ).json()

    line_items = [
        {"project_id": 1001, "kind": "Service", "description": "Dev", "unit_price": 175.0, "quantity": 10.0}
    ]
    harvest_payload = {
        "client_id": 9001,
        "issue_date": "2026-04-30",
        "due_date": "2026-05-30",
        "payment_term": "net30",
        "line_items": line_items,
        "notes": "",
    }
    act = (
        await client.post(
            f"/workflows/{wf['id']}/actions",
            json={
                "agent_slug": test_agent_slug,
                "action_type": "generate_invoice",
                "summary": "Generate invoice for Acme Corp",
                "proposed_payload": {
                    "harvest_payload": harvest_payload,
                    "client_name": "Acme Corp",
                    "subtotal": 1750.0,
                    "line_items": line_items,
                },
                "risk_level": "medium",
            },
        )
    ).json()

    with patch(
        "app.services.execution.harvest.create_invoice_draft",
        new=AsyncMock(return_value=FAKE_INVOICE_RESULT),
    ):
        resp = await client.post(f"/actions/{act['id']}/approve")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "completed"
    assert data["result"]["invoice_id"] == 55001
    assert data["result"]["invoice_number"] == "INV-2026-001"


@pytest.mark.asyncio
async def test_execution_generate_invoice_strips_private_fields(client: AsyncClient, test_agent_slug: str):
    """Private _ fields in line items are stripped before the Harvest API call."""
    wf = (
        await client.post(
            "/workflows",
            json={"kind": "invoice_generation", "trigger_source": "manual", "initiated_by": "system"},
        )
    ).json()

    line_items_with_private = [
        {
            "project_id": 1001,
            "kind": "Service",
            "description": "Dev",
            "unit_price": 175.0,
            "quantity": 10.0,
            "_billing_model": "time_and_materials",
            "_project_name": "Acme Platform",
        }
    ]
    act = (
        await client.post(
            f"/workflows/{wf['id']}/actions",
            json={
                "agent_slug": test_agent_slug,
                "action_type": "generate_invoice",
                "summary": "Generate invoice",
                "proposed_payload": {
                    "harvest_payload": {
                        "client_id": 9001,
                        "issue_date": "2026-04-30",
                        "due_date": "2026-05-30",
                        "payment_term": "net30",
                        "line_items": line_items_with_private,
                        "notes": "",
                    },
                    "client_name": "Acme",
                    "subtotal": 1750.0,
                    "line_items": line_items_with_private,
                },
            },
        )
    ).json()

    captured: list[dict] = []

    async def capture_create(cfg, payload):
        captured.append(payload)
        return FAKE_INVOICE_RESULT

    with patch("app.services.execution.harvest.create_invoice_draft", new=capture_create):
        await client.post(f"/actions/{act['id']}/approve")

    assert captured, "create_invoice_draft was not called"
    sent_items = captured[0]["line_items"]
    for item in sent_items:
        assert not any(k.startswith("_") for k in item), f"Private field in Harvest payload: {item}"


# ── Stub action types ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_invoice_stub_raises(client: AsyncClient, test_agent_slug: str):
    """send_invoice action raises NotImplementedError in v1 — execution marks the action failed."""
    wf = (
        await client.post(
            "/workflows",
            json={"kind": "invoice_send", "trigger_source": "manual", "initiated_by": "system"},
        )
    ).json()
    act = (
        await client.post(
            f"/workflows/{wf['id']}/actions",
            json={
                "agent_slug": test_agent_slug,
                "action_type": "send_invoice",
                "summary": "Send invoice 55001",
                "proposed_payload": {"invoice_id": 55001},
                "risk_level": "high",
            },
        )
    ).json()

    resp = await client.post(f"/actions/{act['id']}/approve")
    # NotImplementedError → 501 Not Implemented
    assert resp.status_code == 501
    assert "not active in v1" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_delete_invoice_stub_raises(client: AsyncClient, test_agent_slug: str):
    """delete_invoice action raises NotImplementedError in v1 — returns 501."""
    wf = (
        await client.post(
            "/workflows",
            json={"kind": "invoice_deletion", "trigger_source": "manual", "initiated_by": "system"},
        )
    ).json()
    act = (
        await client.post(
            f"/workflows/{wf['id']}/actions",
            json={
                "agent_slug": test_agent_slug,
                "action_type": "delete_invoice",
                "summary": "Delete draft invoice 55001",
                "proposed_payload": {"invoice_id": 55001},
                "risk_level": "high",
            },
        )
    ).json()

    resp = await client.post(f"/actions/{act['id']}/approve")
    assert resp.status_code == 501
    assert "not active in v1" in resp.json()["detail"]


# ── Analytics agent ───────────────────────────────────────────────────────────

def test_analytics_run_raises():
    """invoice-analytics cannot be triggered as a workflow."""
    from app.agents.invoice_analytics import InvoiceAnalyticsAgent
    import asyncio

    agent = InvoiceAnalyticsAgent(agent_id=uuid.UUID(int=0), config={})
    with pytest.raises(NotImplementedError, match="read-only chat agent"):
        asyncio.get_event_loop().run_until_complete(
            agent.run(workflow_id=uuid.uuid4(), context={})
        )
