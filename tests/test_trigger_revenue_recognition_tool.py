"""End-to-end tests for the inlined `trigger_revenue_recognition` tool
(ADR-0002, plan 18).

Replaces the deleted `test_rev_rec_graph.py`. The integration mocks
patch the tool module's namespace (where it actually imports airtable,
harvest, forecast, calc_revenue, airtable_sync) and the executor module
for the create-records write.
"""
from __future__ import annotations

import uuid
from contextlib import ExitStack
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from httpx import AsyncClient

from app.agents.tools.base import AwaitingApproval, Blocked, ToolContext
from app.agents.tools.revenue.trigger_revenue_recognition import (
    TRIGGER_REVENUE_RECOGNITION,
    _trigger_revenue_recognition,
)
from app.db import get_pool


def _project(
    *,
    name: str,
    harvest_id: int = 1,
    airtable_id: str = "rec1",
    billing_type: str | None = "Time and Materials",
    client_id: str = "c1",
    contracted_fees: float | None = None,
) -> dict[str, Any]:
    return {
        "Project Name": name,
        "Harvest Id": harvest_id,
        "airtableId": airtable_id,
        "Billing Type": billing_type,
        "Client Id": client_id,
        "Contracted Fees": contracted_fees,
    }


_TOOL = "app.agents.tools.revenue.trigger_revenue_recognition"
_EXEC = "app.executors.write_rev_rec_entries"


def _patch_integrations(
    *,
    projects: list[dict[str, Any]],
    create_records: list[dict[str, Any]] | None = None,
    most_recent: dict[str, Any] | None = None,
) -> dict[str, AsyncMock]:
    """Build the standard set of mocks for one test run.

    Returns the mock objects keyed by short name; the caller applies them
    via `_apply_patches`. Splitting mock construction from patching lets
    tests assert on the mocks after the patched block exits.
    """
    if create_records is None:
        create_records = [{"id": f"rec_new_{i}"} for i in range(len(projects))]
    return {
        "get_most_recent": AsyncMock(return_value=most_recent),
        "get_projects": AsyncMock(return_value=projects),
        "create_revenue_records": AsyncMock(return_value=create_records),
        "scheduled_hours": AsyncMock(return_value={}),
        "invoice_totals": AsyncMock(return_value={}),
        "time_entries": AsyncMock(return_value=10.0),
        "run_sync": AsyncMock(return_value=None),
    }


def _apply_patches(mocks: dict[str, AsyncMock]) -> list:
    return [
        patch(f"{_TOOL}.airtable.get_most_recent_revenue_entry", mocks["get_most_recent"]),
        patch(f"{_TOOL}.airtable.get_projects", mocks["get_projects"]),
        patch(f"{_EXEC}.airtable.create_revenue_records", mocks["create_revenue_records"]),
        patch(f"{_TOOL}.forecast.get_scheduled_hours_by_harvest_id", mocks["scheduled_hours"]),
        patch(f"{_TOOL}.harvest.get_invoice_totals_by_project", mocks["invoice_totals"]),
        patch(f"{_TOOL}.harvest.get_time_entries", mocks["time_entries"]),
        patch(f"{_TOOL}.airtable_sync.run_sync", mocks["run_sync"]),
        patch(f"{_TOOL}.calc_revenue", lambda *_a, **_k: (1234.56, 0.5, "")),
    ]


def _ctx() -> ToolContext:
    return ToolContext(agent_id=uuid.UUID(int=0), agent_slug="revenue-ops")


@pytest.mark.asyncio
async def test_happy_path_returns_awaiting_approval():
    projects = [
        _project(name="Acme Build", harvest_id=101, airtable_id="recA"),
        _project(name="Beta Run", harvest_id=102, airtable_id="recB"),
    ]
    mocks = _patch_integrations(projects=projects)

    with ExitStack() as stack:
        for cm in _apply_patches(mocks):
            stack.enter_context(cm)

        result = await _trigger_revenue_recognition(
            _ctx(), date_recognized="2025-04-30"
        )

    assert isinstance(result, AwaitingApproval)
    assert result.executor == "write_rev_rec_entries"
    assert result.action_type == "write_rev_rec"
    assert result.risk_level == "low"
    assert "2025-04-30" in result.summary
    assert "$" in result.summary

    payload = result.payload
    assert payload["date_recognized"] == "2025-04-30"
    assert len(payload["entries"]) == 2
    assert payload["entries"][0]["Project Name"] == "Acme Build"
    # Underscore-prefixed scratch field survives into the proposed_payload
    # (it's stripped at executor time, not here).
    assert "_blended_rate" in payload["entries"][0]

    # No Airtable write — executor hasn't run.
    mocks["create_revenue_records"].assert_not_called()


@pytest.mark.asyncio
async def test_incomplete_projects_returns_blocked():
    projects = [
        _project(name="Good", harvest_id=201, airtable_id="recG"),
        _project(name="Bad", harvest_id=202, airtable_id="recB", billing_type=None),
    ]
    mocks = _patch_integrations(projects=projects)

    with ExitStack() as stack:
        for cm in _apply_patches(mocks):
            stack.enter_context(cm)

        result = await _trigger_revenue_recognition(
            _ctx(), date_recognized="2025-04-30"
        )

    assert isinstance(result, Blocked)
    assert "1 project(s) need configuration" in result.reason
    assert result.hint is not None
    assert len(result.hint["incomplete_projects"]) == 1
    assert result.hint["incomplete_projects"][0]["project_name"] == "Bad"
    assert "Billing Type" in result.hint["incomplete_projects"][0]["missing_fields"]
    assert result.hint["date_recognized"] == "2025-04-30"

    # No write triggered.
    mocks["create_revenue_records"].assert_not_called()


@pytest.mark.asyncio
async def test_duplicate_run_returns_blocked():
    mocks = _patch_integrations(
        projects=[_project(name="Solo")],
        most_recent={"Date Recognized": "2025-04-30T00:00:00"},
    )

    with ExitStack() as stack:
        for cm in _apply_patches(mocks):
            stack.enter_context(cm)

        result = await _trigger_revenue_recognition(
            _ctx(), date_recognized="2025-04-30"
        )

    assert isinstance(result, Blocked)
    assert "already exist" in result.reason
    assert result.hint == {
        "most_recent_date": "2025-04-30",
        "date_recognized": "2025-04-30",
    }

    # Duplicate guard short-circuits before sync runs.
    mocks["run_sync"].assert_not_called()
    mocks["get_projects"].assert_not_called()
    mocks["create_revenue_records"].assert_not_called()


@pytest.mark.asyncio
async def test_approve_runs_executor_and_writes(
    client: AsyncClient, test_agent_slug
):
    """Full chain: dispatch_tool → approval row → POST /approve → executor → Airtable write."""
    from app.orchestrator.dispatch import dispatch_tool

    projects = [
        _project(name="Acme Build", harvest_id=101, airtable_id="recA"),
        _project(name="Beta Run", harvest_id=102, airtable_id="recB"),
    ]
    mocks = _patch_integrations(projects=projects)
    pool = await get_pool()

    with ExitStack() as stack:
        for cm in _apply_patches(mocks):
            stack.enter_context(cm)

        result_dict = await dispatch_tool(
            TRIGGER_REVENUE_RECOGNITION,
            _ctx(),
            {"date_recognized": "2025-04-30"},
        )
        assert result_dict["status"] == "awaiting_approval"
        approval_id = result_dict["approval_id"]

        appr = await pool.fetchrow(
            "SELECT * FROM approvals WHERE id = $1", UUID(approval_id)
        )
        assert appr["status"] == "pending"
        assert appr["executor"] == "write_rev_rec_entries"
        assert appr["workflow_id"] is None
        assert appr["action_type"] == "write_rev_rec"

        # Still no Airtable write.
        mocks["create_revenue_records"].assert_not_called()

        resp = await client.post(
            f"/approvals/{approval_id}/approve",
            json={"approved_by": "tester"},
        )
        assert resp.status_code == 200, resp.text

    appr_after = await pool.fetchrow(
        "SELECT status FROM approvals WHERE id = $1", UUID(approval_id)
    )
    assert appr_after["status"] == "executed"

    mocks["create_revenue_records"].assert_called_once()
    _settings, written = mocks["create_revenue_records"].call_args.args
    assert len(written) == 2
    # Scratch fields stripped at executor time.
    for entry in written:
        assert not any(k.startswith("_") for k in entry.keys())


@pytest.mark.asyncio
async def test_approve_with_edited_entries(
    client: AsyncClient, test_agent_slug
):
    from app.orchestrator.dispatch import dispatch_tool

    projects = [_project(name="Solo", harvest_id=301, airtable_id="recS")]
    mocks = _patch_integrations(projects=projects)
    await get_pool()

    with ExitStack() as stack:
        for cm in _apply_patches(mocks):
            stack.enter_context(cm)

        result_dict = await dispatch_tool(
            TRIGGER_REVENUE_RECOGNITION,
            _ctx(),
            {"date_recognized": "2025-04-30"},
        )
        approval_id = result_dict["approval_id"]

        edited_payload = {
            "date_recognized": "2025-04-30",
            "total_recognized": 9999.99,
            "entries": [
                {
                    "Harvest Id": 301,
                    "Project Name": "Solo (edited)",
                    "Date Recognized": "2025-04-30",
                    "Total Recognized Revenue": 9999.99,
                    "Percentage Complete": 0.75,
                    "Scheduled Hours": 40,
                    "Logged Hours": 10,
                    "Contracted Fees": None,
                    "Billing Type": "Time and Materials",
                    "Total Projected Hours": 50,
                    "Notes": "human override",
                    "Invoiced to Date": 0,
                    "Project Id": ["recS"],
                    "_blended_rate": 999.99,  # Scratch — must be stripped.
                }
            ],
        }
        resp = await client.post(
            f"/approvals/{approval_id}/approve",
            json={"approved_by": "tester", "executed_payload": edited_payload},
        )
        assert resp.status_code == 200, resp.text

    mocks["create_revenue_records"].assert_called_once()
    _settings, written = mocks["create_revenue_records"].call_args.args
    assert len(written) == 1
    assert written[0]["Project Name"] == "Solo (edited)"
    assert written[0]["Total Recognized Revenue"] == 9999.99
    # Scratch field stripped.
    assert "_blended_rate" not in written[0]


@pytest.mark.asyncio
async def test_reject_skips_airtable_write(client: AsyncClient, test_agent_slug):
    from app.orchestrator.dispatch import dispatch_tool

    projects = [_project(name="Solo", harvest_id=301, airtable_id="recS")]
    mocks = _patch_integrations(projects=projects)
    pool = await get_pool()

    with ExitStack() as stack:
        for cm in _apply_patches(mocks):
            stack.enter_context(cm)

        result_dict = await dispatch_tool(
            TRIGGER_REVENUE_RECOGNITION,
            _ctx(),
            {"date_recognized": "2025-04-30"},
        )
        approval_id = result_dict["approval_id"]

        resp = await client.post(
            f"/approvals/{approval_id}/reject",
            json={"rejected_by": "tester", "rejection_reason": "numbers look off"},
        )
        assert resp.status_code == 200, resp.text

    appr_after = await pool.fetchrow(
        "SELECT status FROM approvals WHERE id = $1", UUID(approval_id)
    )
    assert appr_after["status"] == "rejected"

    mocks["create_revenue_records"].assert_not_called()
