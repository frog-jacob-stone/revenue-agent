"""End-to-end tests for the outreach_chain graph.

Stubs `call_anthropic` so the graph can drive its three agents
(outreach-agent, voice-critic, accuracy-critic) without network. The
fake dispatches by prompt marker so each test scenario can declare what
each role should return.

Five scenarios:
  - happy: voice + accuracy both pass on first try → pause at gmail_send
  - voice_loop: voice fails once, then passes → still terminates at the gate
  - voice_exhausted: voice fails 3 times → failed_terminal
  - accuracy_exhausted: voice passes; accuracy fails 2 times → failed_terminal
  - reject_at_gmail_send: approval rejected → workflow failed
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.db import get_pool
from app.orchestrator import runner
from app.orchestrator.graphs.outreach import (
    ACTION_TYPE_SEND,
    OUTREACH_AGENT_SLUG,
    OUTREACH_KIND,
    build_graph,
)


@pytest.fixture(autouse=True)
def _register_graph():
    if not runner.is_registered(OUTREACH_KIND):
        runner.register(OUTREACH_KIND, build_graph)
    yield
    runner.unregister(OUTREACH_KIND)


# ── Fake LLM dispatcher ──────────────────────────────────────────────────────


def _make_fake_call(*, voice_results: list[bool], accuracy_results: list[bool]):
    """Build an async fake for call_anthropic.

    `voice_results` and `accuracy_results` are popped (FIFO) on each critique.
    The `consolidate` brief and `draft` email are returned for all non-critique
    calls — the prompt text discriminates which role is being invoked.
    """
    voice = list(voice_results)
    accuracy = list(accuracy_results)

    async def fake(prompt: str, *, model: str, max_tokens: int) -> str:
        if "Voice Critic" in prompt:
            passed = voice.pop(0) if voice else False
            return json.dumps({
                "passed": passed,
                "score": 0.9 if passed else 0.3,
                "feedback": "ok" if passed else "too generic",
                "issues": [] if passed else ["cliché opener"],
            })
        if "Accuracy Critic" in prompt:
            passed = accuracy.pop(0) if accuracy else False
            return json.dumps({
                "passed": passed,
                "score": 0.9 if passed else 0.3,
                "feedback": "supported" if passed else "fabricated detail",
                "issues": [] if passed else ["claim X not in signals"],
            })
        if 'Output JSON: {"subject"' in prompt:
            return json.dumps({"subject": "Quick question", "body": "Hi there."})
        if "produce a 3-4 sentence brief" in prompt:
            return "Acme Corp is hiring backend engineers and just raised a Series B."
        return "[unhandled stub]"

    return fake


def _payload(approval) -> dict:
    p = approval["proposed_payload"]
    return json.loads(p) if isinstance(p, str) else p


# ── Scenarios ────────────────────────────────────────────────────────────────


async def test_happy_path_voice_pass_accuracy_pass(client: AsyncClient):
    """Both critics pass on first try → graph pauses at the gmail_send gate
    with the draft as the proposed_payload."""
    fake = _make_fake_call(voice_results=[True], accuracy_results=[True])
    with patch("app.orchestrator.agent_invoke.call_anthropic", side_effect=fake):
        wf_id = await runner.start(
            OUTREACH_KIND,
            initial_state={"hubspot_contact_id": "stub-001"},
        )

    pool = await get_pool()
    wf = await pool.fetchrow("SELECT status FROM workflows WHERE id = $1", wf_id)
    assert wf["status"] == "awaiting_approval"

    appr = await pool.fetchrow(
        "SELECT * FROM approvals WHERE workflow_id = $1", wf_id
    )
    assert appr["status"] == "pending"
    assert appr["action_type"] == ACTION_TYPE_SEND
    assert appr["agent_slug"] == OUTREACH_AGENT_SLUG

    payload = _payload(appr)
    assert payload["subject"] == "Quick question"
    assert payload["body"] == "Hi there."
    assert payload["to"]  # filled in from contact stub


async def test_voice_loop_passes_after_one_retry(client: AsyncClient):
    """Voice fails once with budget remaining → redraft → voice passes →
    accuracy passes → pause at gmail_send. Voice attempts == 2 in final state."""
    fake = _make_fake_call(voice_results=[False, True], accuracy_results=[True])
    with patch("app.orchestrator.agent_invoke.call_anthropic", side_effect=fake):
        wf_id = await runner.start(
            OUTREACH_KIND,
            initial_state={"hubspot_contact_id": "stub-002"},
        )

    pool = await get_pool()
    wf = await pool.fetchrow("SELECT status FROM workflows WHERE id = $1", wf_id)
    # Should still pause at gmail_send — the loop terminates by passing voice.
    assert wf["status"] == "awaiting_approval"

    appr = await pool.fetchrow(
        "SELECT status FROM approvals WHERE workflow_id = $1", wf_id
    )
    assert appr["status"] == "pending"


async def test_voice_budget_exhausted_terminates(client: AsyncClient):
    """Voice fails 3 times (default max) → failed_terminal → workflow completed
    with no approval row created (no gmail_send gate reached)."""
    fake = _make_fake_call(
        voice_results=[False, False, False], accuracy_results=[],
    )
    with patch("app.orchestrator.agent_invoke.call_anthropic", side_effect=fake):
        wf_id = await runner.start(
            OUTREACH_KIND,
            initial_state={"hubspot_contact_id": "stub-003"},
        )

    pool = await get_pool()
    wf = await pool.fetchrow("SELECT status FROM workflows WHERE id = $1", wf_id)
    # Terminal failure node still ends at END — workflow completes cleanly.
    assert wf["status"] == "completed"

    appr_count = await pool.fetchval(
        "SELECT COUNT(*) FROM approvals WHERE workflow_id = $1", wf_id
    )
    assert appr_count == 0  # never reached the send gate


async def test_accuracy_budget_exhausted_terminates(client: AsyncClient):
    """Voice passes each time; accuracy fails twice (default max=2) → terminal.

    Sequence: voice pass → accuracy fail (loop) → voice pass → accuracy fail →
    failed_terminal. Voice runs again on each new draft so we need two voice
    passes; accuracy runs twice and both fail.
    """
    fake = _make_fake_call(
        voice_results=[True, True],
        accuracy_results=[False, False],
    )
    with patch("app.orchestrator.agent_invoke.call_anthropic", side_effect=fake):
        wf_id = await runner.start(
            OUTREACH_KIND,
            initial_state={"hubspot_contact_id": "stub-004"},
        )

    pool = await get_pool()
    wf = await pool.fetchrow("SELECT status FROM workflows WHERE id = $1", wf_id)
    assert wf["status"] == "completed"

    appr_count = await pool.fetchval(
        "SELECT COUNT(*) FROM approvals WHERE workflow_id = $1", wf_id
    )
    assert appr_count == 0


async def test_reject_at_gmail_send_fails_workflow(client: AsyncClient):
    """Reject at the gmail_send gate → workflow failed."""
    fake = _make_fake_call(voice_results=[True], accuracy_results=[True])
    with patch("app.orchestrator.agent_invoke.call_anthropic", side_effect=fake):
        wf_id = await runner.start(
            OUTREACH_KIND,
            initial_state={"hubspot_contact_id": "stub-005"},
        )

    pool = await get_pool()
    appr = await pool.fetchrow(
        "SELECT id FROM approvals WHERE workflow_id = $1", wf_id
    )

    resp = await client.post(
        f"/approvals/{appr['id']}/reject",
        json={"rejected_by": "tester", "rejection_reason": "wrong contact"},
    )
    assert resp.status_code == 200, resp.text
    await runner.resume(wf_id)

    wf_after = await pool.fetchrow("SELECT status FROM workflows WHERE id = $1", wf_id)
    assert wf_after["status"] == "failed"
