"""End-to-end tests for the rev_rec_monthly graph.

Drives the runner against a graph whose external integrations
(airtable, harvest, forecast) are stubbed. Three paths exercised:

  - happy: all projects valid → compute → propose write → approve → write
  - incomplete: a project is missing fields → pause at configure gate
  - reject: write_entries gate rejected → workflow failed, no Airtable write

The integrations are patched on the graph module's imported names — calls
inside `validate_and_sync`/`compute_entries`/`write_entries` resolve to the
patched callables for the duration of the test.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.db import get_pool
from app.orchestrator import runner
from app.orchestrator.graphs.rev_rec import (
    ACTION_TYPE_CONFIGURE,
    ACTION_TYPE_WRITE,
    REV_REC_AGENT_SLUG,
    REV_REC_KIND,
    build_graph,
)


@pytest.fixture(autouse=True)
def _register_graph():
    if not runner.is_registered(REV_REC_KIND):
        runner.register(REV_REC_KIND, build_graph)
    yield
    runner.unregister(REV_REC_KIND)


def _project(*, name: str, harvest_id: int = 1, airtable_id: str = "rec1",
             billing_type: str = "Time and Materials", client_id: str = "c1",
             contracted_fees: float | None = None) -> dict:
    return {
        "Project Name": name,
        "Harvest Id": harvest_id,
        "airtableId": airtable_id,
        "Billing Type": billing_type,
        "Client Id": client_id,
        "Contracted Fees": contracted_fees,
    }


def _patch_integrations(*, projects: list[dict], create_records: list[dict] | None = None):
    """Build the standard set of mocks for one test run."""
    create_records = create_records if create_records is not None else [
        {"id": f"rec_new_{i}"} for i in range(len(projects))
    ]
    return {
        "airtable.get_most_recent_revenue_entry": AsyncMock(return_value=None),
        "airtable.get_projects": AsyncMock(return_value=projects),
        "airtable.create_revenue_records": AsyncMock(return_value=create_records),
        "forecast.get_scheduled_hours_by_harvest_id": AsyncMock(return_value={}),
        "harvest.get_invoice_totals_by_project": AsyncMock(return_value={}),
        "harvest.get_time_entries": AsyncMock(return_value=10.0),
        "airtable_sync.run_sync": AsyncMock(return_value=None),
        "revenue.calc_revenue": lambda *_a, **_k: (1234.56, 0.5, ""),
    }


def _apply_patches(mocks: dict):
    """Returns a list of context managers that patch the graph module's
    imported names. Use under a contextlib.ExitStack."""
    g = "app.orchestrator.graphs.rev_rec"
    return [
        patch(f"{g}.airtable.get_most_recent_revenue_entry", mocks["airtable.get_most_recent_revenue_entry"]),
        patch(f"{g}.airtable.get_projects", mocks["airtable.get_projects"]),
        patch(f"{g}.airtable.create_revenue_records", mocks["airtable.create_revenue_records"]),
        patch(f"{g}.forecast.get_scheduled_hours_by_harvest_id", mocks["forecast.get_scheduled_hours_by_harvest_id"]),
        patch(f"{g}.harvest.get_invoice_totals_by_project", mocks["harvest.get_invoice_totals_by_project"]),
        patch(f"{g}.harvest.get_time_entries", mocks["harvest.get_time_entries"]),
        patch(f"{g}.airtable_sync.run_sync", mocks["airtable_sync.run_sync"]),
        patch(f"{g}.calc_revenue", mocks["revenue.calc_revenue"]),
    ]


def _payload(approval) -> dict:
    p = approval["proposed_payload"]
    return json.loads(p) if isinstance(p, str) else p


async def test_happy_path_writes_revenue_entries(client: AsyncClient):
    """All projects valid → graph drives validate → compute → propose_write_entries
    → pauses at write_entries gate → approve → write_entries → completed."""
    projects = [
        _project(name="Acme Build", harvest_id=101, airtable_id="recA"),
        _project(name="Beta Run", harvest_id=102, airtable_id="recB"),
    ]
    mocks = _patch_integrations(projects=projects)

    from contextlib import ExitStack
    with ExitStack() as stack:
        for cm in _apply_patches(mocks):
            stack.enter_context(cm)

        wf_id = await runner.start(
            REV_REC_KIND,
            initial_state={"date_recognized": "2025-04-30"},
        )

        pool = await get_pool()
        wf = await pool.fetchrow("SELECT status FROM workflows WHERE id = $1", wf_id)
        assert wf["status"] == "awaiting_approval"

        appr = await pool.fetchrow(
            "SELECT * FROM approvals WHERE workflow_id = $1", wf_id
        )
        assert appr["status"] == "pending"
        assert appr["action_type"] == ACTION_TYPE_WRITE
        assert appr["agent_slug"] == REV_REC_AGENT_SLUG

        payload = _payload(appr)
        assert payload["date_recognized"] == "2025-04-30"
        assert len(payload["entries"]) == 2
        assert payload["entries"][0]["Project Name"] == "Acme Build"

        # No Airtable write yet — gate hasn't been crossed.
        mocks["airtable.create_revenue_records"].assert_not_called()

        # Approve via HTTP.
        resp = await client.post(
            f"/approvals/{appr['id']}/approve",
            json={"approved_by": "tester"},
        )
        assert resp.status_code == 200, resp.text
        await runner.resume(wf_id)

        wf_after = await pool.fetchrow(
            "SELECT status FROM workflows WHERE id = $1", wf_id
        )
        assert wf_after["status"] == "completed"

        # The graph called create_revenue_records exactly once with cleaned entries.
        mocks["airtable.create_revenue_records"].assert_called_once()
        _settings, written = mocks["airtable.create_revenue_records"].call_args.args
        assert len(written) == 2
        # Underscore-prefixed scratch fields stripped before write.
        for entry in written:
            assert not any(k.startswith("_") for k in entry.keys())

        appr_after = await pool.fetchrow(
            "SELECT status FROM approvals WHERE id = $1", appr["id"]
        )
        assert appr_after["status"] == "executed"


async def test_incomplete_projects_pause_at_configure_gate(client: AsyncClient):
    """A project missing Billing Type → graph routes to propose_configure and
    pauses at apply_configure_or_loop. The configure approval surfaces the
    incomplete-project payload to the inbox."""
    projects = [
        _project(name="Good", harvest_id=201, airtable_id="recG"),
        _project(name="Bad", harvest_id=202, airtable_id="recB", billing_type=None),
    ]
    mocks = _patch_integrations(projects=projects)

    from contextlib import ExitStack
    with ExitStack() as stack:
        for cm in _apply_patches(mocks):
            stack.enter_context(cm)

        wf_id = await runner.start(
            REV_REC_KIND,
            initial_state={"date_recognized": "2025-04-30"},
        )

        pool = await get_pool()
        wf = await pool.fetchrow("SELECT status FROM workflows WHERE id = $1", wf_id)
        assert wf["status"] == "awaiting_approval"

        appr = await pool.fetchrow(
            "SELECT * FROM approvals WHERE workflow_id = $1", wf_id
        )
        assert appr["status"] == "pending"
        assert appr["action_type"] == ACTION_TYPE_CONFIGURE

        payload = _payload(appr)
        assert len(payload["incomplete_projects"]) == 1
        assert payload["incomplete_projects"][0]["project_name"] == "Bad"
        assert "Billing Type" in payload["incomplete_projects"][0]["missing_fields"]

        # No Airtable write — we never reached compute_entries.
        mocks["airtable.create_revenue_records"].assert_not_called()


async def test_reject_at_write_entries_fails_workflow(client: AsyncClient):
    """Rejecting the write_entries approval marks the workflow failed and skips
    the Airtable write entirely."""
    projects = [_project(name="Solo", harvest_id=301, airtable_id="recS")]
    mocks = _patch_integrations(projects=projects)

    from contextlib import ExitStack
    with ExitStack() as stack:
        for cm in _apply_patches(mocks):
            stack.enter_context(cm)

        wf_id = await runner.start(
            REV_REC_KIND,
            initial_state={"date_recognized": "2025-04-30"},
        )

        pool = await get_pool()
        appr = await pool.fetchrow(
            "SELECT id FROM approvals WHERE workflow_id = $1", wf_id
        )

        resp = await client.post(
            f"/approvals/{appr['id']}/reject",
            json={"rejected_by": "tester", "rejection_reason": "numbers look off"},
        )
        assert resp.status_code == 200, resp.text
        await runner.resume(wf_id)

        wf_after = await pool.fetchrow(
            "SELECT status FROM workflows WHERE id = $1", wf_id
        )
        assert wf_after["status"] == "failed"

        # Reject path never invokes the Airtable write.
        mocks["airtable.create_revenue_records"].assert_not_called()
