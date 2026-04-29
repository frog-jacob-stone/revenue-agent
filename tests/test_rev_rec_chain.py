"""Tests for the rev_rec_monthly chain.

Replaces the legacy `RevenueRecognitionAgent.run()` -> propose-action flow.
Integrations (Airtable, Harvest, Forecast, airtable_sync) are patched so the
chain runs deterministically without real services.

Two divergent endings — "ready" → write to Airtable, "incomplete" → checkpoint
that requeues — are exercised through the same chain via skip_if predicates.

Step handlers are captured on the step instances at construction time, so
patches must target the instance attributes (`step._handler`, `step._executor`,
`step._on_approve`) rather than the module-level symbols.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from unittest.mock import patch

import asyncpg
import pytest
from httpx import AsyncClient

from app.db import get_pool
from app.orchestrator import orchestrator
from app.orchestrator.chain import _reset_registry_for_tests
from app.orchestrator.chains import rev_rec as rev_rec_chain


@pytest.fixture(autouse=True)
def _register_rev_rec_chain():
    _reset_registry_for_tests()
    rev_rec_chain.register()
    yield
    _reset_registry_for_tests()


@contextmanager
def _patch_chain_handlers(
    *,
    sync_validate=None,
    compute=None,
    write=None,
    on_configure_approved=None,
):
    """Patch the handlers bound to each step instance in the rev rec chain.

    Constructed-at-import-time references can't be replaced via module-level
    `patch.object`, so we override the attributes on each step instance for
    the duration of the test.
    """
    chain = rev_rec_chain.REV_REC_CHAIN
    sync_step = chain.steps[0]
    compute_step = chain.steps[1]
    checkpoint_step = chain.steps[2]
    execution_step = chain.steps[3]
    patches = []
    if sync_validate is not None:
        patches.append(patch.object(sync_step, "_handler", sync_validate))
    if compute is not None:
        patches.append(patch.object(compute_step, "_handler", compute))
    if write is not None:
        patches.append(patch.object(execution_step, "_executor", write))
    if on_configure_approved is not None:
        patches.append(patch.object(checkpoint_step, "_on_approve", on_configure_approved))

    for p in patches:
        p.start()
    try:
        yield
    finally:
        for p in patches:
            p.stop()


@pytest.fixture(scope="session")
async def rev_rec_agent_id(_test_pool: asyncpg.Pool) -> uuid.UUID:
    return await _test_pool.fetchval(
        """
        INSERT INTO agents (slug, name, requires_approval, approval_scope, config, is_active)
        VALUES ($1, 'Revenue Recognition', true, '{create,update,delete}'::text[],
                '{}'::jsonb, true)
        ON CONFLICT (slug) DO UPDATE SET is_active = true
        RETURNING id
        """,
        rev_rec_chain.REV_REC_AGENT_SLUG,
    )


async def _fetch_actions(workflow_id: uuid.UUID) -> list[asyncpg.Record]:
    pool = await get_pool()
    return await pool.fetch(
        "SELECT * FROM actions WHERE workflow_id = $1 ORDER BY sequence",
        workflow_id,
    )


async def _fetch_workflow(workflow_id: uuid.UUID) -> asyncpg.Record:
    pool = await get_pool()
    return await pool.fetchrow("SELECT * FROM workflows WHERE id = $1", workflow_id)


# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------

async def test_ready_path_proposes_write_and_executes(
    client: AsyncClient,
    rev_rec_agent_id: uuid.UUID,
) -> None:
    """All projects valid → chain skips checkpoint, reaches execution step,
    approval writes to Airtable."""
    validate_result = {
        "status": "ready",
        "date_recognized": "2026-03-31",
        "month_label": "2026-03",
        "projects": [{"airtableId": "rec1"}],
        "context": {},
    }
    compute_result = {
        "date_recognized": "2026-03-31",
        "entries": [
            {"Project Name": "P1", "Total Recognized Revenue": 1000.0, "_blended_rate": 100.0}
        ],
        "total_recognized": 1000.0,
    }
    written: list[dict] = []

    async def fake_validate(ctx):
        return validate_result

    async def fake_compute(ctx):
        return compute_result

    async def fake_write(ctx):
        written.append(ctx.executed_payload)
        return {"records_created": 1, "airtable_ids": ["recX"]}

    with _patch_chain_handlers(
        sync_validate=fake_validate,
        compute=fake_compute,
        write=fake_write,
    ):
        wf_id = await orchestrator.start(rev_rec_chain.REV_REC_KIND)

        actions = await _fetch_actions(wf_id)
        # Validate (auto), Compute (auto), Execution (proposed). Checkpoint skipped.
        assert [a["step_kind"] for a in actions] == ["tool_call", "tool_call", "execution"]
        assert actions[-1]["status"] == "proposed"
        assert actions[-1]["action_type"] == "write_rev_rec"
        assert actions[-1]["proposed_payload"]["entries"] == compute_result["entries"]

        # Approve and run executor.
        approve = await client.post(
            f"/actions/{actions[-1]['id']}/approve",
            json={"approved_by": "tester"},
        )
        assert approve.status_code == 200
        await orchestrator.resume(wf_id)

    final = await _fetch_actions(wf_id)
    assert final[-1]["status"] == "completed"
    assert final[-1]["result"]["records_created"] == 1
    assert written, "executor was not called"

    wf = await _fetch_workflow(wf_id)
    assert wf["status"] == "completed"


async def test_incomplete_path_proposes_checkpoint_and_requeues(
    client: AsyncClient,
    rev_rec_agent_id: uuid.UUID,
) -> None:
    """Validation fails → chain reaches checkpoint, skipping compute and
    execution. Approving the checkpoint runs `on_approve`, which queues a
    new validation cycle."""
    validate_result = {
        "status": "incomplete",
        "date_recognized": "2026-03-31",
        "month_label": "2026-03",
        "incomplete_projects": [
            {"project_name": "Bad Project", "missing_fields": ["Billing Type"]}
        ],
        "context": {"date_recognized": "2026-03-31"},
    }

    async def fake_validate(ctx):
        return validate_result

    async def fake_compute(ctx):
        raise AssertionError("compute should be skipped on incomplete validation")

    async def fake_write(ctx):
        raise AssertionError("write should be skipped on incomplete validation")

    requeue_calls: list[dict] = []

    async def fake_on_approve(ctx):
        requeue_calls.append(ctx.executed_payload or {})
        return {"requeued_workflow_id": "00000000-0000-0000-0000-000000000000"}

    with _patch_chain_handlers(
        sync_validate=fake_validate,
        compute=fake_compute,
        write=fake_write,
        on_configure_approved=fake_on_approve,
    ):
        wf_id = await orchestrator.start(rev_rec_chain.REV_REC_KIND)

        actions = await _fetch_actions(wf_id)
        # Validate (auto), Checkpoint (proposed). Compute and Execution skipped.
        assert [a["step_kind"] for a in actions] == ["tool_call", "checkpoint"]
        assert actions[-1]["status"] == "proposed"
        assert actions[-1]["action_type"] == "configure_rev_rec_projects"
        assert actions[-1]["proposed_payload"]["incomplete_projects"]

        # Approve checkpoint -> on_approve runs -> chain advances and skips
        # remaining steps.
        approve = await client.post(
            f"/actions/{actions[-1]['id']}/approve",
            json={"approved_by": "tester"},
        )
        assert approve.status_code == 200
        await orchestrator.resume(wf_id)

    final = await _fetch_actions(wf_id)
    assert final[-1]["status"] == "completed"
    assert len(requeue_calls) == 1, "on_approve should have been called once"

    wf = await _fetch_workflow(wf_id)
    assert wf["status"] == "completed"


async def test_incomplete_rejection_cancels_workflow(
    client: AsyncClient,
    rev_rec_agent_id: uuid.UUID,
) -> None:
    """Rejecting the configure-projects checkpoint cancels the orchestrated
    workflow (matches the standard rejection-cancel-pattern from Phase B)."""
    validate_result = {
        "status": "incomplete",
        "date_recognized": "2026-03-31",
        "month_label": "2026-03",
        "incomplete_projects": [{"project_name": "P", "missing_fields": ["Client Id"]}],
        "context": {},
    }

    async def fake_validate(ctx):
        return validate_result

    with _patch_chain_handlers(sync_validate=fake_validate):
        wf_id = await orchestrator.start(rev_rec_chain.REV_REC_KIND)

    actions = await _fetch_actions(wf_id)
    checkpoint = actions[-1]
    assert checkpoint["step_kind"] == "checkpoint"

    reject = await client.post(
        f"/actions/{checkpoint['id']}/reject",
        json={"rejection_reason": "skipping this month"},
    )
    assert reject.status_code == 200

    wf = await _fetch_workflow(wf_id)
    assert wf["status"] == "cancelled"


async def test_revenue_recognition_agent_trigger_uses_orchestrator(
    rev_rec_agent_id: uuid.UUID,
) -> None:
    """RevenueRecognitionAgent.trigger() returns a workflow_id from the
    orchestrator and runs the chain (no agent_runner involvement)."""
    from app.agents.revenue_recognition import RevenueRecognitionAgent

    async def fake_validate(ctx):
        return {
            "status": "incomplete",
            "date_recognized": "2026-03-31",
            "month_label": "2026-03",
            "incomplete_projects": [],
            "context": {},
        }

    with _patch_chain_handlers(sync_validate=fake_validate):
        result = await RevenueRecognitionAgent.trigger(
            context={"date_recognized": "2026-03-31"}
        )

    assert "workflow_id" in result
    wf = await _fetch_workflow(uuid.UUID(result["workflow_id"]))
    assert wf is not None
    assert wf["pattern"] == "supervised_automation"
    assert wf["kind"] == rev_rec_chain.REV_REC_KIND
