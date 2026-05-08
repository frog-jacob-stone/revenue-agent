"""Tests for the prompt-chain orchestrator.

Uses fake step handlers (no LLM calls) and the existing test fixtures from
conftest.py for per-test rollback.

Each test registers its own chain in the chain registry and tears it down via
_reset_registry_for_tests so tests are independent.
"""
import uuid
from typing import Any

import asyncpg
import pytest
from httpx import AsyncClient

from app.db import get_pool
from app.models.workflows import WorkflowPattern
from app.orchestrator import (
    Chain,
    CheckpointStep,
    CritiqueStep,
    ExecutionStep,
    LLMStep,
    StepContext,
    TaskStep,
    orchestrator,
    register_chain,
)
from app.orchestrator.chain import _reset_registry_for_tests


@pytest.fixture(autouse=True)
def _reset_chain_registry():
    """Each test sees a clean chain registry."""
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


# ----- Helpers ---------------------------------------------------------------


async def _fetch_actions(workflow_id: uuid.UUID) -> list[asyncpg.Record]:
    pool = await get_pool()
    return await pool.fetch(
        "SELECT * FROM actions WHERE workflow_id = $1 ORDER BY sequence",
        workflow_id,
    )


async def _fetch_workflow(workflow_id: uuid.UUID) -> asyncpg.Record:
    pool = await get_pool()
    return await pool.fetchrow("SELECT * FROM workflows WHERE id = $1", workflow_id)


def _const_handler(value: dict[str, Any]):
    async def handler(ctx: StepContext) -> dict[str, Any]:
        return value
    return handler


# ----- Tests -----------------------------------------------------------------


async def test_three_step_auto_chain_completes(test_agent_slug: str) -> None:
    """A chain of three auto-progressing steps runs end-to-end without pause."""
    register_chain(Chain(
        kind="test_auto_chain",
        pattern=WorkflowPattern.prompt_chain_action,
        agent_slug=test_agent_slug,
        steps=(
            TaskStep("Step 1: tool", _const_handler({"step": 1})),
            LLMStep("Step 2: llm", _const_handler({"step": 2})),
            LLMStep("Step 3: llm", _const_handler({"step": 3})),
        ),
    ))

    workflow_id = await orchestrator.start("test_auto_chain")

    actions = await _fetch_actions(workflow_id)
    assert [a["step_kind"] for a in actions] == ["task", "llm_step", "llm_step"]
    assert all(a["status"] == "completed" for a in actions)
    assert [a["result"] for a in actions] == [
        {"step": 1}, {"step": 2}, {"step": 3},
    ]

    wf = await _fetch_workflow(workflow_id)
    assert wf["status"] == "completed"
    assert wf["pattern"] == "prompt_chain_action"
    assert wf["completed_at"] is not None


async def test_checkpoint_pauses_workflow(test_agent_slug: str) -> None:
    """A checkpoint step stops the workflow and surfaces the action for approval."""
    register_chain(Chain(
        kind="test_checkpoint_chain",
        pattern=WorkflowPattern.prompt_chain_action,
        agent_slug=test_agent_slug,
        steps=(
            LLMStep("Draft", _const_handler({"draft": "hello"})),
            CheckpointStep("Approve draft"),
        ),
    ))

    workflow_id = await orchestrator.start("test_checkpoint_chain")

    actions = await _fetch_actions(workflow_id)
    assert len(actions) == 2
    assert actions[0]["status"] == "completed"
    assert actions[1]["step_kind"] == "checkpoint"
    assert actions[1]["status"] == "proposed"
    # CheckpointStep's default propose() surfaces the prior step's result.
    assert actions[1]["proposed_payload"] == {"draft": "hello"}

    wf = await _fetch_workflow(workflow_id)
    assert wf["status"] == "awaiting_approval"
    assert wf["current_step"] == 1


async def test_checkpoint_approval_resumes_to_completion(
    client: AsyncClient, test_agent_slug: str
) -> None:
    """Approving a checkpoint via the API resumes the chain to the next pause/end."""
    register_chain(Chain(
        kind="test_resume_chain",
        pattern=WorkflowPattern.prompt_chain_action,
        agent_slug=test_agent_slug,
        steps=(
            LLMStep("Draft", _const_handler({"draft": "hello"})),
            CheckpointStep("Approve draft"),
            LLMStep("Finalize", _const_handler({"final": "done"})),
        ),
    ))
    workflow_id = await orchestrator.start("test_resume_chain")

    pending = (await _fetch_actions(workflow_id))[1]
    resp = await client.post(f"/actions/{pending['id']}/approve")
    assert resp.status_code == 200

    # BackgroundTask isn't dispatched in the test transaction; call resume directly.
    await orchestrator.resume(workflow_id)

    actions = await _fetch_actions(workflow_id)
    assert [a["status"] for a in actions] == ["completed", "completed", "completed"]
    wf = await _fetch_workflow(workflow_id)
    assert wf["status"] == "completed"


async def test_critique_pass_advances(test_agent_slug: str) -> None:
    """A critique that passes on first try advances without writing a retry."""
    register_chain(Chain(
        kind="test_critique_pass",
        pattern=WorkflowPattern.prompt_chain_action,
        agent_slug=test_agent_slug,
        steps=(
            LLMStep("Draft", _const_handler({"draft": "good"})),
            CritiqueStep(
                "Voice check",
                _const_handler({"passed": True, "score": 0.9, "feedback": "ok", "issues": []}),
                critiques_step_index=0,
                max_attempts=3,
            ),
        ),
    ))
    workflow_id = await orchestrator.start("test_critique_pass")

    actions = await _fetch_actions(workflow_id)
    assert [a["step_kind"] for a in actions] == ["llm_step", "critique"]
    assert all(a["status"] == "completed" for a in actions)
    assert actions[1]["critique_result"]["passed"] is True

    wf = await _fetch_workflow(workflow_id)
    assert wf["status"] == "completed"


async def test_critique_fail_then_pass_writes_retries(test_agent_slug: str) -> None:
    """Critique fails twice, third time passes. Two retries of the draft are written."""
    # Stateful handlers: track how many times they've been called.
    draft_calls = {"n": 0}
    critique_calls = {"n": 0}

    async def draft_handler(ctx: StepContext) -> dict[str, Any]:
        draft_calls["n"] += 1
        return {"draft": f"v{draft_calls['n']}", "saw_feedback": ctx.critique_feedback is not None}

    async def critique_handler(ctx: StepContext) -> dict[str, Any]:
        critique_calls["n"] += 1
        passed = critique_calls["n"] >= 3  # fail on 1st and 2nd attempts
        return {"passed": passed, "score": 0.5, "feedback": "needs work", "issues": []}

    register_chain(Chain(
        kind="test_critique_fail_then_pass",
        pattern=WorkflowPattern.prompt_chain_action,
        agent_slug=test_agent_slug,
        steps=(
            LLMStep("Draft", draft_handler),
            CritiqueStep("Voice check", critique_handler, critiques_step_index=0, max_attempts=3),
        ),
    ))
    workflow_id = await orchestrator.start("test_critique_fail_then_pass")

    actions = await _fetch_actions(workflow_id)
    # Expected: draft v1, critique fail, draft v2, critique fail, draft v3, critique pass
    assert [a["step_kind"] for a in actions] == [
        "llm_step", "critique", "llm_step", "critique", "llm_step", "critique"
    ]
    assert [a["attempt_number"] for a in actions] == [1, 1, 2, 2, 3, 3]
    # Retries point back via retry_of_action_id.
    assert actions[2]["retry_of_action_id"] == actions[0]["id"]
    assert actions[3]["retry_of_action_id"] == actions[1]["id"]
    # Draft retries received critique feedback.
    assert actions[2]["result"]["saw_feedback"] is True
    assert actions[4]["result"]["saw_feedback"] is True
    # Final critique passed.
    assert actions[5]["critique_result"]["passed"] is True

    wf = await _fetch_workflow(workflow_id)
    assert wf["status"] == "completed"


async def test_critique_exhausts_budget_marks_failed(test_agent_slug: str) -> None:
    """Critique fails max_attempts times → workflow failed, audit captures error."""
    async def always_fail(ctx: StepContext) -> dict[str, Any]:
        return {"passed": False, "score": 0.1, "feedback": "no", "issues": ["bad voice"]}

    register_chain(Chain(
        kind="test_critique_exhaust",
        pattern=WorkflowPattern.prompt_chain_action,
        agent_slug=test_agent_slug,
        steps=(
            LLMStep("Draft", _const_handler({"draft": "x"})),
            CritiqueStep("Voice check", always_fail, critiques_step_index=0, max_attempts=2),
        ),
    ))
    workflow_id = await orchestrator.start("test_critique_exhaust")

    actions = await _fetch_actions(workflow_id)
    # Expected: draft1, critique1 fail → draft2, critique2 fail → workflow failed
    assert [a["step_kind"] for a in actions] == [
        "llm_step", "critique", "llm_step", "critique"
    ]
    assert [a["attempt_number"] for a in actions] == [1, 1, 2, 2]

    wf = await _fetch_workflow(workflow_id)
    assert wf["status"] == "failed"
    assert "max_attempts" in (wf["error"] or "")

    # Audit log captured the workflow.failed event.
    pool = await get_pool()
    events = await pool.fetch(
        "SELECT event_type FROM audit_log WHERE workflow_id = $1 ORDER BY occurred_at",
        workflow_id,
    )
    assert "workflow.failed" in [e["event_type"] for e in events]


async def test_execution_step_runs_after_approval(
    client: AsyncClient, test_agent_slug: str
) -> None:
    """ExecutionStep pauses for approval, then its executor runs on resume."""
    executed_with: dict[str, Any] = {}

    async def executor(ctx: StepContext) -> dict[str, Any]:
        executed_with["payload"] = ctx.executed_payload
        return {"sent": True, "to": "test@example.com"}

    register_chain(Chain(
        kind="test_execution",
        pattern=WorkflowPattern.prompt_chain_action,
        agent_slug=test_agent_slug,
        steps=(
            LLMStep("Draft", _const_handler({"to": "test@example.com", "body": "hi"})),
            ExecutionStep("Send email", executor),
        ),
    ))
    workflow_id = await orchestrator.start("test_execution")

    pending = (await _fetch_actions(workflow_id))[1]
    assert pending["step_kind"] == "execution"
    assert pending["status"] == "proposed"

    resp = await client.post(
        f"/actions/{pending['id']}/approve",
        json={"approved_by": "tester", "executed_payload": {"to": "test@example.com", "body": "hi (edited)"}},
    )
    assert resp.status_code == 200

    await orchestrator.resume(workflow_id)

    actions = await _fetch_actions(workflow_id)
    assert actions[1]["status"] == "completed"
    assert actions[1]["result"] == {"sent": True, "to": "test@example.com"}
    assert executed_with["payload"]["body"] == "hi (edited)"

    wf = await _fetch_workflow(workflow_id)
    assert wf["status"] == "completed"


async def test_checkpoint_rejection_cancels_orchestrated_workflow(
    client: AsyncClient, test_agent_slug: str
) -> None:
    """Rejecting a checkpoint marks the orchestrated workflow cancelled."""
    register_chain(Chain(
        kind="test_reject",
        pattern=WorkflowPattern.prompt_chain_action,
        agent_slug=test_agent_slug,
        steps=(
            LLMStep("Draft", _const_handler({"draft": "x"})),
            CheckpointStep("Approve"),
            LLMStep("Never", _const_handler({"never": True})),
        ),
    ))
    workflow_id = await orchestrator.start("test_reject")

    pending = (await _fetch_actions(workflow_id))[1]
    resp = await client.post(
        f"/actions/{pending['id']}/reject",
        json={"rejection_reason": "off voice"},
    )
    assert resp.status_code == 200

    actions = await _fetch_actions(workflow_id)
    assert len(actions) == 2  # never wrote step 3
    assert actions[1]["status"] == "rejected"
    assert actions[1]["rejection_reason"] == "off voice"

    wf = await _fetch_workflow(workflow_id)
    assert wf["status"] == "cancelled"
    assert "off voice" in (wf["error"] or "")


async def test_inbox_excludes_internal_steps(client: AsyncClient, test_agent_slug: str) -> None:
    """task/llm_step/critique rows must not appear in GET /actions."""
    register_chain(Chain(
        kind="test_inbox_filter",
        pattern=WorkflowPattern.prompt_chain_action,
        agent_slug=test_agent_slug,
        steps=(
            TaskStep("Internal tool", _const_handler({"x": 1})),
            LLMStep("Internal llm", _const_handler({"x": 2})),
            CheckpointStep("Approve"),
        ),
    ))
    workflow_id = await orchestrator.start("test_inbox_filter")

    inbox = (await client.get("/actions?status=proposed")).json()
    inbox_for_workflow = [a for a in inbox if a["workflow_id"] == str(workflow_id)]
    # Only the checkpoint is visible.
    assert len(inbox_for_workflow) == 1
    assert inbox_for_workflow[0]["step_kind"] == "checkpoint"
