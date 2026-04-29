"""Outreach chain — happy path tests (Phase D).

LLM calls are stubbed at the chain helper level so tests are deterministic
and don't require an Anthropic key. The chain itself is registered fresh in
each test so other tests can clear the registry without breaking these.

Phase E will add critique-loop tests on top of these.
"""
from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from httpx import AsyncClient

from app.db import get_pool
from app.orchestrator import orchestrator
from app.orchestrator.chain import _reset_registry_for_tests
from app.orchestrator.chains import outreach as outreach_chain


@pytest.fixture(autouse=True)
def _register_outreach_chain():
    """Each outreach test gets a clean registry with only the outreach chain."""
    _reset_registry_for_tests()
    outreach_chain.register()
    yield
    _reset_registry_for_tests()


@pytest.fixture(scope="session")
async def outreach_agent_id(_test_pool: asyncpg.Pool) -> uuid.UUID:
    """Insert the outreach agent into the test DB at session scope."""
    return await _test_pool.fetchval(
        """
        INSERT INTO agents (slug, name, requires_approval, approval_scope, config, is_active)
        VALUES ($1, 'Outreach Agent', true, '{create,update,delete}'::text[],
                '{}'::jsonb, true)
        ON CONFLICT (slug) DO UPDATE SET is_active = true
        RETURNING id
        """,
        outreach_chain.OUTREACH_AGENT_SLUG,
    )


@pytest.fixture
def stub_complete():
    """Patch the chain's LLM helper with deterministic responses keyed by step.

    Use as `with stub_complete({...}) as calls: ...` — calls is a list of
    every (prompt, max_tokens) pair the chain made.
    """
    def make(responses: dict[str, str]):
        calls: list[tuple[str, int]] = []

        async def fake(ctx, prompt: str, *, max_tokens: int) -> str:
            calls.append((prompt, max_tokens))
            for marker, response in responses.items():
                if marker in prompt:
                    return response
            return "<fallback>"

        return patch.object(outreach_chain, "_complete", side_effect=fake), calls

    return make


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

async def test_outreach_chain_pauses_at_execution(
    client: AsyncClient,
    outreach_agent_id: uuid.UUID,
    stub_complete,
) -> None:
    """End-to-end: trigger outreach, run prep steps, pause at execution gate."""
    # Markers are checked in dict order — put the more specific one (the draft
    # prompt's "Output JSON:") first so it doesn't fall through to the BRIEF rule.
    patcher, calls = stub_complete({
        "Output JSON:": json.dumps({
            "subject": "Backend scale at Acme",
            "body": "Saw the Series B announcement — would 15 minutes Thursday make sense?",
        }),
        "3-4 sentence brief": "Acme just raised $20M and is scaling backend eng.",
    })

    with patcher:
        resp = await client.post(
            "/workflows/outreach",
            json={"hubspot_contact_id": "hs-001", "initiated_by": "tester"},
        )
        assert resp.status_code == 202, resp.text
        workflow_id = uuid.UUID(resp.json()["workflow_id"])

        # Background task runs after response; tests drive it explicitly.
        await orchestrator.resume(workflow_id)

    actions = await _fetch_actions(workflow_id)
    assert [a["step_kind"] for a in actions] == [
        "tool_call", "tool_call", "llm_step", "tool_call", "llm_step", "execution",
    ]
    # Execution step is the approval gate; everything before is done.
    assert [a["status"] for a in actions[:-1]] == ["completed"] * 5
    assert actions[-1]["status"] == "proposed"

    # Execution step surfaces the draft as proposed_payload for review.
    proposed = actions[-1]["proposed_payload"]
    assert proposed["subject"] == "Backend scale at Acme"
    assert "Series B" in proposed["body"]

    # Stub LLM was called twice (consolidate, draft).
    assert len(calls) == 2

    wf = await _fetch_workflow(workflow_id)
    assert wf["status"] == "awaiting_approval"
    assert wf["pattern"] == "prompt_chain_action"
    assert wf["subject_type"] == "contact"
    assert wf["subject_id"] == "hs-001"


async def test_outreach_approval_runs_gmail_stub_and_completes(
    client: AsyncClient,
    outreach_agent_id: uuid.UUID,
    stub_complete,
) -> None:
    """Approving the execution step triggers the Gmail-send stub; workflow completes."""
    patcher, _ = stub_complete({
        "Output JSON:": json.dumps({"subject": "S", "body": "B"}),
        "3-4 sentence brief": "ok",
    })
    with patcher:
        resp = await client.post(
            "/workflows/outreach", json={"hubspot_contact_id": "hs-002"},
        )
        workflow_id = uuid.UUID(resp.json()["workflow_id"])
        await orchestrator.resume(workflow_id)

    pending = (await _fetch_actions(workflow_id))[-1]
    assert pending["step_kind"] == "execution"

    edited = {"subject": "S (edited)", "body": "B (edited)", "to": "ceo@example"}
    approve = await client.post(
        f"/actions/{pending['id']}/approve",
        json={"approved_by": "tester", "executed_payload": edited},
    )
    assert approve.status_code == 200

    # BackgroundTask doesn't run reliably under the test transaction; resume directly.
    await orchestrator.resume(workflow_id)

    actions = await _fetch_actions(workflow_id)
    # 5 auto + 1 execution (approved → side effect → completed) = 6 rows.
    assert len(actions) == 6
    assert [a["step_kind"] for a in actions] == [
        "tool_call", "tool_call", "llm_step", "tool_call", "llm_step", "execution",
    ]
    assert actions[-1]["status"] == "completed"
    assert actions[-1]["result"]["stub"] is True
    assert actions[-1]["result"]["would_send_to"] == "ceo@example"
    # Edited payload made it through to the executor.
    assert actions[-1]["result"]["subject"] == "S (edited)"

    wf = await _fetch_workflow(workflow_id)
    assert wf["status"] == "completed"
    assert wf["completed_at"] is not None


async def test_outreach_rejection_at_execution_cancels(
    client: AsyncClient,
    outreach_agent_id: uuid.UUID,
    stub_complete,
) -> None:
    """Rejecting the execution gate marks the workflow cancelled; Gmail never runs."""
    patcher, _ = stub_complete({
        "Output JSON:": json.dumps({"subject": "S", "body": "B"}),
        "3-4 sentence brief": "ok",
    })
    with patcher:
        resp = await client.post(
            "/workflows/outreach", json={"hubspot_contact_id": "hs-003"},
        )
        workflow_id = uuid.UUID(resp.json()["workflow_id"])
        await orchestrator.resume(workflow_id)

    pending = (await _fetch_actions(workflow_id))[-1]
    reject = await client.post(
        f"/actions/{pending['id']}/reject",
        json={"rejection_reason": "tone is off"},
    )
    assert reject.status_code == 200

    actions = await _fetch_actions(workflow_id)
    assert len(actions) == 6
    assert actions[-1]["status"] == "rejected"

    wf = await _fetch_workflow(workflow_id)
    assert wf["status"] == "cancelled"
    assert "tone is off" in (wf["error"] or "")


async def test_outreach_inbox_only_shows_execution(
    client: AsyncClient,
    outreach_agent_id: uuid.UUID,
    stub_complete,
) -> None:
    """Inbox hides tool_call/llm_step rows; only the execution gate appears."""
    patcher, _ = stub_complete({
        "Output JSON:": json.dumps({"subject": "S", "body": "B"}),
        "3-4 sentence brief": "ok",
    })
    with patcher:
        resp = await client.post(
            "/workflows/outreach", json={"hubspot_contact_id": "hs-004"},
        )
        workflow_id = uuid.UUID(resp.json()["workflow_id"])
        await orchestrator.resume(workflow_id)

    inbox = (await client.get("/actions?status=proposed")).json()
    inbox_for_workflow = [a for a in inbox if a["workflow_id"] == str(workflow_id)]
    assert len(inbox_for_workflow) == 1
    assert inbox_for_workflow[0]["step_kind"] == "execution"
