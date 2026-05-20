"""End-to-end tests for the outreach_chain graph.

Uses `use_provider(FakeProvider(respond=...))` so the graph's four LLM call
sites (consolidate brief, compose email, voice critique, accuracy critique)
run without network. The respond callable dispatches by prompt marker so each
test scenario declares what each role should return.

Five scenarios:
  - happy: voice + accuracy both pass on first try → pause at gmail_send
  - voice_loop: voice fails once, then passes → still terminates at the gate
  - voice_exhausted: voice fails 3 times → failed_terminal
  - accuracy_exhausted: voice passes; accuracy fails 2 times → failed_terminal
  - reject_at_gmail_send: approval rejected → workflow failed
"""
from __future__ import annotations

import json

import pytest
from httpx import AsyncClient

from app.agents.bdr import BDRAgent
from app.db import get_pool
from app.integrations.llm import LlmResponse, use_provider
from app.orchestrator import runner
from app.orchestrator.graphs.outreach import (
    ACTION_TYPE_SEND,
    OUTREACH_KIND,
    build_graph,
)
from tests.fakes.llm import FakeProvider


@pytest.fixture(autouse=True)
def _register_graph():
    if not runner.is_registered(OUTREACH_KIND):
        runner.register(OUTREACH_KIND, build_graph)
    yield
    runner.unregister(OUTREACH_KIND)


# ── Provider router ─────────────────────────────────────────────────────────


def _make_provider(*, voice_results: list[bool], accuracy_results: list[bool]) -> FakeProvider:
    """Build a FakeProvider that dispatches by prompt marker.

    `voice_results` and `accuracy_results` are popped (FIFO) on each critique.
    """
    voice = list(voice_results)
    accuracy = list(accuracy_results)

    def respond(request: dict) -> LlmResponse:
        messages = request.get("messages", [])
        prompt = "\n".join(m.get("content", "") or "" for m in messages)
        if "Voice Critic" in prompt:
            passed = voice.pop(0) if voice else False
            return LlmResponse(
                text=json.dumps({
                    "passed": passed,
                    "score": 0.9 if passed else 0.3,
                    "feedback": "ok" if passed else "too generic",
                    "issues": [] if passed else ["cliché opener"],
                }),
                finish_reason="stop",
            )
        if "Accuracy Critic" in prompt:
            passed = accuracy.pop(0) if accuracy else False
            return LlmResponse(
                text=json.dumps({
                    "passed": passed,
                    "score": 0.9 if passed else 0.3,
                    "feedback": "supported" if passed else "fabricated detail",
                    "issues": [] if passed else ["claim X not in signals"],
                }),
                finish_reason="stop",
            )
        if 'Output JSON: {"subject"' in prompt:
            return LlmResponse(
                text=json.dumps({"subject": "Quick question", "body": "Hi there."}),
                finish_reason="stop",
            )
        if "produce a 3-4 sentence brief" in prompt:
            return LlmResponse(
                text="Acme Corp is hiring backend engineers and just raised a Series B.",
                finish_reason="stop",
            )
        return LlmResponse(text="[unhandled stub]", finish_reason="stop")

    return FakeProvider(respond=respond)


def _payload(approval) -> dict:
    p = approval["proposed_payload"]
    return json.loads(p) if isinstance(p, str) else p


# ── Scenarios ────────────────────────────────────────────────────────────────


async def test_happy_path_voice_pass_accuracy_pass(client: AsyncClient):
    """Both critics pass on first try → graph pauses at the gmail_send gate
    with the draft as the proposed_payload."""
    provider = _make_provider(voice_results=[True], accuracy_results=[True])
    with use_provider(provider):
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
    assert appr["agent_slug"] == BDRAgent.slug  # outreach attribution = BDR (owning agent)

    payload = _payload(appr)
    assert payload["subject"] == "Quick question"
    assert payload["body"] == "Hi there."
    assert payload["to"]  # filled in from contact stub


async def test_voice_loop_passes_after_one_retry(client: AsyncClient):
    """Voice fails once with budget remaining → redraft → voice passes →
    accuracy passes → pause at gmail_send. Voice attempts == 2 in final state."""
    provider = _make_provider(voice_results=[False, True], accuracy_results=[True])
    with use_provider(provider):
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
    provider = _make_provider(
        voice_results=[False, False, False], accuracy_results=[],
    )
    with use_provider(provider):
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
    provider = _make_provider(
        voice_results=[True, True],
        accuracy_results=[False, False],
    )
    with use_provider(provider):
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
    provider = _make_provider(voice_results=[True], accuracy_results=[True])
    with use_provider(provider):
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
