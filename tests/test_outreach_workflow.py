"""Outreach chain tests — happy path + critique loops.

LLM calls are stubbed at the chain helper level so tests are deterministic
and don't require an Anthropic key. The chain itself is registered fresh in
each test so other tests can clear the registry without breaking these.
"""
from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import patch

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
    """Insert the outreach agent + critic agents into the test DB.

    The chain references three agent slugs (outreach-agent, voice-critic,
    accuracy-critic); orchestrator step writes fail with a 500 if any are
    missing or inactive.
    """
    for slug in (
        outreach_chain.OUTREACH_AGENT_SLUG,
        outreach_chain.VOICE_CRITIC_SLUG,
        outreach_chain.ACCURACY_CRITIC_SLUG,
    ):
        await _test_pool.execute(
            """
            INSERT INTO agents (slug, config, is_active)
            VALUES ($1, '{}'::jsonb, true)
            ON CONFLICT (slug) DO UPDATE SET is_active = true
            """,
            slug,
        )
    return await _test_pool.fetchval(
        "SELECT id FROM agents WHERE slug = $1",
        outreach_chain.OUTREACH_AGENT_SLUG,
    )


_PASS_CRITIQUE = json.dumps({
    "passed": True, "score": 0.9, "feedback": "ok", "issues": [],
})


@pytest.fixture
def stub_complete():
    """Patch the chain's LLM helper with deterministic responses.

    Defaults critic prompts to "pass" so tests that don't care about the
    critique loop can ignore those markers. Override per-test by passing a
    response keyed on "Voice Critic" or "Accuracy Critic".
    """
    def make(responses: dict[str, str]):
        calls: list[tuple[str, int]] = []
        # Default critics to pass; specific tests can override these markers.
        merged: dict[str, str] = {
            "Voice Critic": _PASS_CRITIQUE,
            "Accuracy Critic": _PASS_CRITIQUE,
            **responses,
        }

        async def fake(prompt: str, *, model: str, max_tokens: int) -> str:
            calls.append((prompt, max_tokens))
            # Match in caller-provided order first, then defaults; the user's
            # responses dict is iterated last so it can override defaults.
            for marker, response in merged.items():
                if marker in prompt:
                    return response
            return "<fallback>"

        return patch.object(outreach_chain, "call_anthropic", side_effect=fake), calls

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
        "task", "task", "llm_step", "task", "llm_step",
        "critique", "critique", "execution",
    ]
    # Execution step is the approval gate; everything before is done.
    assert [a["status"] for a in actions[:-1]] == ["completed"] * 7
    assert actions[-1]["status"] == "proposed"

    # Execution step surfaces the draft as proposed_payload for review.
    proposed = actions[-1]["proposed_payload"]
    assert proposed["subject"] == "Backend scale at Acme"
    assert "Series B" in proposed["body"]

    # LLM was called four times: consolidate, draft, voice critique, accuracy critique.
    assert len(calls) == 4

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
    # 5 auto + 2 critiques (passed) + 1 execution (approved → side effect) = 8 rows.
    assert len(actions) == 8
    assert [a["step_kind"] for a in actions] == [
        "task", "task", "llm_step", "task", "llm_step",
        "critique", "critique", "execution",
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
    # 5 auto + 2 critiques + 1 execution = 8 rows; execution is rejected.
    assert len(actions) == 8
    assert actions[-1]["status"] == "rejected"

    wf = await _fetch_workflow(workflow_id)
    assert wf["status"] == "cancelled"
    assert "tone is off" in (wf["error"] or "")


async def test_outreach_inbox_only_shows_execution(
    client: AsyncClient,
    outreach_agent_id: uuid.UUID,
    stub_complete,
) -> None:
    """Inbox hides task/llm_step/critique rows; only the execution gate appears."""
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


# -----------------------------------------------------------------------------
# Critique loop tests (Phase E)
# -----------------------------------------------------------------------------

def _critique_response(passed: bool, *, feedback: str = "", issues: list[str] | None = None) -> str:
    return json.dumps({
        "passed": passed,
        "score": 0.95 if passed else 0.4,
        "feedback": feedback or ("ok" if passed else "needs revision"),
        "issues": issues or [],
    })


@pytest.fixture
def stateful_complete():
    """Stateful stub: track call count per marker so tests can answer
    differently on attempt 1 vs attempt 2 vs ..."""
    def make(scripts: dict[str, list[str]]):
        cursors: dict[str, int] = {k: 0 for k in scripts}
        calls: list[str] = []

        async def fake(prompt: str, *, model: str, max_tokens: int) -> str:
            for marker, responses in scripts.items():
                if marker in prompt:
                    idx = min(cursors[marker], len(responses) - 1)
                    cursors[marker] += 1
                    calls.append(marker)
                    return responses[idx]
            calls.append("<unmatched>")
            return "<fallback>"

        return patch.object(outreach_chain, "call_anthropic", side_effect=fake), calls, cursors

    return make


async def test_voice_critique_failure_then_pass_writes_retries(
    client: AsyncClient,
    outreach_agent_id: uuid.UUID,
    stateful_complete,
) -> None:
    """Voice critique fails twice, passes on third attempt → two retries of the
    draft step are written; chain reaches the execution gate."""
    draft_first = json.dumps({"subject": "v1", "body": "Hi there, hope this finds you well."})
    draft_v2 = json.dumps({"subject": "v2", "body": "Saw the round; 15 min Thu?"})
    draft_v3 = json.dumps({"subject": "v3", "body": "Saw the round; 15 min Thursday or Friday?"})

    patcher, calls, _ = stateful_complete({
        "3-4 sentence brief": ["A brief about Acme."],
        "Output JSON:": [draft_first, draft_v2, draft_v3],
        "Voice Critic": [
            _critique_response(False, feedback="Too generic; cliché opener.", issues=["cliché opener"]),
            _critique_response(False, feedback="Still off-voice.", issues=["weak ask"]),
            _critique_response(True, feedback="On voice."),
        ],
        "Accuracy Critic": [_critique_response(True)],
    })

    with patcher:
        resp = await client.post(
            "/workflows/outreach", json={"hubspot_contact_id": "hs-voice"},
        )
        workflow_id = uuid.UUID(resp.json()["workflow_id"])
        await orchestrator.resume(workflow_id)

    actions = await _fetch_actions(workflow_id)
    # Expected: 4 prep + draft v1 + voice fail + draft v2 + voice fail +
    # draft v3 + voice pass + accuracy pass + execution. = 4 + 3 + 3 + 1 = 11 rows.
    step_kinds = [a["step_kind"] for a in actions]
    assert step_kinds == [
        "task", "task", "llm_step", "task",
        "llm_step", "critique",          # draft v1, voice fail
        "llm_step", "critique",          # draft v2, voice fail
        "llm_step", "critique",          # draft v3, voice pass
        "critique",                      # accuracy pass
        "execution",                     # gate
    ]
    # Draft retries chain back to the original.
    drafts = [a for a in actions if a["step_kind"] == "llm_step" and a["sequence"] >= 5]
    assert len(drafts) == 3
    assert drafts[0]["attempt_number"] == 1
    assert drafts[0]["retry_of_action_id"] is None
    assert drafts[1]["attempt_number"] == 2
    assert drafts[1]["retry_of_action_id"] == drafts[0]["id"]
    assert drafts[2]["attempt_number"] == 3
    assert drafts[2]["retry_of_action_id"] == drafts[1]["id"]

    # Final draft saw critique feedback (we passed it via ctx.critique_feedback).
    # The third draft handler was invoked with attempt_number=3.
    # We can't introspect prompt content from here, but call counts confirm.
    assert calls.count("Voice Critic") == 3
    assert calls.count("Output JSON:") == 3
    assert calls.count("Accuracy Critic") == 1

    # Last action is the execution gate, awaiting approval.
    assert actions[-1]["step_kind"] == "execution"
    assert actions[-1]["status"] == "proposed"

    wf = await _fetch_workflow(workflow_id)
    assert wf["status"] == "awaiting_approval"


async def test_voice_critique_exhausted_fails_workflow(
    client: AsyncClient,
    outreach_agent_id: uuid.UUID,
    stateful_complete,
) -> None:
    """Voice critique fails 3 times (max_attempts=3) → workflow marked failed."""
    draft = json.dumps({"subject": "S", "body": "B"})
    patcher, calls, _ = stateful_complete({
        "3-4 sentence brief": ["brief"],
        "Output JSON:": [draft, draft, draft],
        "Voice Critic": [
            _critique_response(False, feedback="bad"),
            _critique_response(False, feedback="bad"),
            _critique_response(False, feedback="bad"),
        ],
    })

    with patcher:
        resp = await client.post(
            "/workflows/outreach", json={"hubspot_contact_id": "hs-exhaust"},
        )
        workflow_id = uuid.UUID(resp.json()["workflow_id"])
        await orchestrator.resume(workflow_id)

    actions = await _fetch_actions(workflow_id)
    # 3 draft attempts + 3 voice critiques (all failed) = no accuracy, no execution.
    assert calls.count("Voice Critic") == 3
    assert calls.count("Accuracy Critic") == 0
    voice_actions = [a for a in actions if a["step_kind"] == "critique"]
    assert all(not a["critique_result"]["passed"] for a in voice_actions)

    wf = await _fetch_workflow(workflow_id)
    assert wf["status"] == "failed"
    assert "max_attempts" in (wf["error"] or "")

    # workflow.failed event is in the audit log.
    pool = await get_pool()
    events = await pool.fetch(
        "SELECT event_type FROM audit_log WHERE workflow_id = $1 ORDER BY occurred_at",
        workflow_id,
    )
    assert "workflow.failed" in [e["event_type"] for e in events]


async def test_accuracy_critique_runs_after_voice_passes(
    client: AsyncClient,
    outreach_agent_id: uuid.UUID,
    stateful_complete,
) -> None:
    """Voice passes first try; accuracy fails once then passes → one extra
    draft attempt for accuracy, both critiques re-run after the new draft."""
    draft_v1 = json.dumps({"subject": "v1", "body": "claims acme has 5000 employees"})
    draft_v2 = json.dumps({"subject": "v2", "body": "no fabricated headcount this time"})

    patcher, calls, _ = stateful_complete({
        "3-4 sentence brief": ["brief"],
        "Output JSON:": [draft_v1, draft_v2],
        "Voice Critic": [_critique_response(True), _critique_response(True)],
        "Accuracy Critic": [
            _critique_response(False, feedback="headcount is hallucinated", issues=["5000 employees"]),
            _critique_response(True),
        ],
    })

    with patcher:
        resp = await client.post(
            "/workflows/outreach", json={"hubspot_contact_id": "hs-accuracy"},
        )
        workflow_id = uuid.UUID(resp.json()["workflow_id"])
        await orchestrator.resume(workflow_id)

    # Voice runs after each draft; accuracy runs after each draft too (because
    # accuracy's failure rewinds to the draft, which re-runs voice).
    assert calls.count("Voice Critic") == 2
    assert calls.count("Accuracy Critic") == 2
    assert calls.count("Output JSON:") == 2

    actions = await _fetch_actions(workflow_id)
    assert actions[-1]["step_kind"] == "execution"
    assert actions[-1]["status"] == "proposed"

    wf = await _fetch_workflow(workflow_id)
    assert wf["status"] == "awaiting_approval"


async def test_voice_profile_memory_drives_critique(
    client: AsyncClient,
    outreach_agent_id: uuid.UUID,
    stub_complete,
) -> None:
    """The voice critic prompt embeds the seeded voice profile memory.

    We seed a sentinel value into memories and verify it appears in the
    voice critique prompt that the LLM sees.
    """
    pool = await get_pool()
    voice_critic_id = await pool.fetchval(
        "SELECT id FROM agents WHERE slug = $1",
        outreach_chain.VOICE_CRITIC_SLUG,
    )
    sentinel = "FROGSLAYER_VOICE_PROFILE_TEST_SENTINEL_42"
    await pool.execute(
        """
        INSERT INTO memories (agent_id, kind, scope, content, metadata)
        VALUES ($1, 'preference', 'global', $2, $3)
        """,
        voice_critic_id, sentinel, {"kind": "voice_profile"},
    )

    captured: list[str] = []

    async def fake(prompt: str, *, model: str, max_tokens: int) -> str:
        captured.append(prompt)
        if "Voice Critic" in prompt:
            return _critique_response(True)
        if "Accuracy Critic" in prompt:
            return _critique_response(True)
        if "Output JSON:" in prompt:
            return json.dumps({"subject": "S", "body": "B"})
        return "brief"

    with patch.object(outreach_chain, "call_anthropic", side_effect=fake):
        resp = await client.post(
            "/workflows/outreach", json={"hubspot_contact_id": "hs-voiceprof"},
        )
        workflow_id = uuid.UUID(resp.json()["workflow_id"])
        await orchestrator.resume(workflow_id)

    voice_prompts = [p for p in captured if "Voice Critic" in p]
    assert voice_prompts, "voice critique was never invoked"
    assert sentinel in voice_prompts[0], "voice profile memory was not loaded into the critic prompt"
