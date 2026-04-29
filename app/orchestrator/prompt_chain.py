"""Prompt-chain orchestrator.

Drives a Chain forward by writing one action row per step. Auto-progressing
steps run inline; pausing steps (checkpoint, execution) leave the action in
'proposed' status for human review and return.

Critique loop:
  draft (step N)  →  critique (step N+1)
                       │  passed=True  →  advance
                       │  passed=False, budget left  →  retry of step N
                       │  passed=False, budget out   →  workflow failed

Retries are sibling rows of the failed prior attempt: they share step_kind
and (for non-critique steps) a chain step index, but get the next physical
sequence number. Logical step index is computed via the retry chain root.

State is reconstructed from action rows on every entry — the orchestrator
holds no in-memory state between calls.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import asyncpg
from fastapi import HTTPException

from app.db import get_pool
from app.models.workflows import WorkflowPattern
from app.orchestrator.base import BaseOrchestrator
from app.orchestrator.chain import Chain, get_chain
from app.orchestrator.state import ActionRow, StepContext, WorkflowState
from app.orchestrator.steps import (
    CheckpointStep,
    CritiqueStep,
    ExecutionStep,
    LLMStep,
    Step,
    ToolCallStep,
)
from app.services import audit

logger = logging.getLogger(__name__)


_PAUSING_KINDS = {"checkpoint", "execution"}


class PromptChainOrchestrator(BaseOrchestrator):
    async def start(
        self,
        kind: str,
        *,
        context: dict[str, Any] | None = None,
        initiated_by: str = "system",
        trigger_source: str = "manual",
        subject_type: str | None = None,
        subject_id: str | None = None,
        subject_ref: dict[str, Any] | None = None,
    ) -> UUID:
        chain = get_chain(kind)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                workflow_id = await conn.fetchval(
                    """
                    INSERT INTO workflows
                        (kind, status, pattern, current_step,
                         trigger_source, trigger_payload,
                         subject_type, subject_id, subject_ref,
                         initiated_by)
                    VALUES ($1, 'running', $2, 0, $3, $4, $5, $6, $7, $8)
                    RETURNING id
                    """,
                    chain.kind,
                    chain.pattern.value,
                    trigger_source,
                    context or {},
                    subject_type,
                    subject_id,
                    subject_ref,
                    initiated_by,
                )
                await audit.write_audit_event(
                    conn,
                    "workflow.started",
                    workflow_id=workflow_id,
                    actor=initiated_by,
                    payload={"kind": kind, "pattern": chain.pattern.value},
                )

        await self._drive(workflow_id)
        return workflow_id

    async def resume(self, workflow_id: UUID) -> None:
        await self._drive(workflow_id)

    # -------------------------------------------------------------------------
    # Internal: the actual driver
    # -------------------------------------------------------------------------
    async def _drive(self, workflow_id: UUID) -> None:
        """Run steps forward until the workflow pauses, completes, or fails.

        Each iteration takes one logical action: execute the next pending
        step, complete an approved pausing step, or retry after a failed
        critique.
        """
        pool = await get_pool()

        # Loop until we hit a pause or a terminal state. We re-read state
        # every iteration so the loop is restartable from any point.
        while True:
            async with pool.acquire() as conn:
                state = await self._load_state(conn, workflow_id)
                if state is None:
                    return  # workflow not found — nothing to do

                if state.pattern == "":  # not orchestrated
                    return

                # Terminal states: nothing more to drive.
                workflow_status = await conn.fetchval(
                    "SELECT status FROM workflows WHERE id = $1", workflow_id
                )
                if workflow_status in ("completed", "failed", "cancelled"):
                    return

                chain = get_chain(state.kind)

                # Did the previously-pending pausing step just get approved?
                pending = self._find_pending(state)
                if pending is not None:
                    if pending.status == "proposed":
                        return  # still waiting on a human; nothing to do
                    if pending.status == "approved":
                        await self._complete_pausing_step(
                            conn, chain, state, pending
                        )
                        continue  # re-read state and proceed

                # Are we done?
                if state.current_step >= len(chain.steps):
                    await self._mark_completed(conn, workflow_id)
                    return

                step = chain.steps[state.current_step]
                await self._run_next_step(conn, chain, state, step)

    async def _run_next_step(
        self,
        conn: asyncpg.Connection,
        chain: Chain,
        state: WorkflowState,
        step: Step,
    ) -> None:
        """Write and execute (or pause on) the chain's current step."""
        attempt_number, max_attempts, retry_of = self._compute_attempt(state, step)
        agent_id = await self._resolve_agent_id(conn, step.agent_slug or chain.agent_slug)
        critique_feedback = self._collect_critique_feedback(state, chain, step)

        ctx = StepContext(
            workflow_id=state.workflow_id,
            workflow_kind=state.kind,
            step_index=state.current_step,
            attempt_number=attempt_number,
            state=state,
            conn=conn,
            critique_feedback=critique_feedback,
        )

        async with conn.transaction():
            try:
                proposed_payload = await step.propose(ctx)
            except Exception as exc:
                logger.exception(
                    "step propose failed: workflow=%s step=%d kind=%s",
                    state.workflow_id, state.current_step, step.step_kind.value,
                )
                await self._mark_workflow_failed(
                    conn, state.workflow_id, f"step {state.current_step} propose: {exc}"
                )
                return

            action_id = await self._insert_action(
                conn,
                workflow_id=state.workflow_id,
                agent_id=agent_id,
                step=step,
                proposed_payload=proposed_payload,
                attempt_number=attempt_number,
                max_attempts=max_attempts,
                retry_of=retry_of,
            )
            await audit.write_audit_event(
                conn,
                "action.proposed",
                agent_id=agent_id,
                workflow_id=state.workflow_id,
                action_id=action_id,
                actor=f"orchestrator:{chain.kind}",
                payload={
                    "step_index": state.current_step,
                    "step_kind": step.step_kind.value,
                    "summary": step.summary,
                },
            )

            if step.step_kind.value in _PAUSING_KINDS:
                # Leave action in 'proposed'; mark workflow awaiting_approval and stop.
                await conn.execute(
                    "UPDATE workflows SET status = 'awaiting_approval' WHERE id = $1",
                    state.workflow_id,
                )
                await audit.write_audit_event(
                    conn,
                    "workflow.awaiting_approval",
                    workflow_id=state.workflow_id,
                    actor="orchestrator",
                    payload={"step_index": state.current_step},
                )
                # Caller (driver loop) will see pending=proposed and return.
                return

            # Auto-progressing step: complete it inline.
            result = proposed_payload  # propose() doubles as the result for auto kinds
            critique_result = result if isinstance(step, CritiqueStep) else None

            await conn.execute(
                """
                UPDATE actions
                SET status = 'completed',
                    result = $1,
                    critique_result = $2,
                    executed_at = now()
                WHERE id = $3
                """,
                result,
                critique_result,
                action_id,
            )
            await audit.write_audit_event(
                conn,
                "action.completed",
                agent_id=agent_id,
                workflow_id=state.workflow_id,
                action_id=action_id,
                actor=f"orchestrator:{chain.kind}",
                payload={"step_index": state.current_step},
            )

            await self._advance_or_retry(
                conn, chain, state, step, action_id, critique_result
            )

    async def _complete_pausing_step(
        self,
        conn: asyncpg.Connection,
        chain: Chain,
        state: WorkflowState,
        pending: ActionRow,
    ) -> None:
        """Action was approved by a human; run its execute() and advance."""
        step = chain.steps[state.current_step]

        ctx = StepContext(
            workflow_id=state.workflow_id,
            workflow_kind=state.kind,
            step_index=state.current_step,
            attempt_number=pending.attempt_number,
            state=state,
            conn=conn,
            executed_payload=pending.executed_payload or pending.proposed_payload,
        )

        async with conn.transaction():
            await conn.execute(
                "UPDATE actions SET status = 'executing' WHERE id = $1",
                pending.id,
            )
            try:
                result = await step.execute(ctx)
            except Exception as exc:
                logger.exception(
                    "step execute failed: workflow=%s step=%d kind=%s",
                    state.workflow_id, state.current_step, step.step_kind.value,
                )
                await conn.execute(
                    "UPDATE actions SET status = 'failed', error = $1 WHERE id = $2",
                    str(exc),
                    pending.id,
                )
                await audit.write_audit_event(
                    conn,
                    "action.failed",
                    workflow_id=state.workflow_id,
                    action_id=pending.id,
                    actor="orchestrator",
                    payload={"error": str(exc)},
                )
                await self._mark_workflow_failed(
                    conn, state.workflow_id, f"step {state.current_step} execute: {exc}"
                )
                return

            await conn.execute(
                """
                UPDATE actions
                SET status = 'completed',
                    result = $1,
                    executed_at = now()
                WHERE id = $2
                """,
                result,
                pending.id,
            )
            await audit.write_audit_event(
                conn,
                "action.completed",
                workflow_id=state.workflow_id,
                action_id=pending.id,
                actor="orchestrator",
                payload={"step_index": state.current_step},
            )
            # Pausing steps don't trigger critique loops, so just advance.
            await self._set_current_step(conn, state.workflow_id, state.current_step + 1)
            await conn.execute(
                "UPDATE workflows SET status = 'running' WHERE id = $1",
                state.workflow_id,
            )

    async def _advance_or_retry(
        self,
        conn: asyncpg.Connection,
        chain: Chain,
        state: WorkflowState,
        step: Step,
        action_id: UUID,
        critique_result: dict[str, Any] | None,
    ) -> None:
        """After completing an auto-progressing step, decide: advance or retry."""
        if isinstance(step, CritiqueStep) and critique_result is not None:
            passed = bool(critique_result.get("passed", False))
            if not passed:
                target_step = step.critiques_step_index
                attempts_used = state.attempts_for_step(target_step)
                if attempts_used >= step.max_attempts:
                    await self._mark_workflow_failed(
                        conn,
                        state.workflow_id,
                        f"critique step {state.current_step} exhausted "
                        f"max_attempts={step.max_attempts} on step {target_step}",
                    )
                    return
                # Loop back: rewind current_step to the critiqued step. The
                # next driver iteration will write a retry attempt of it.
                await self._set_current_step(conn, state.workflow_id, target_step)
                return

        # Pass (or non-critique step): advance.
        await self._set_current_step(conn, state.workflow_id, state.current_step + 1)

    # -------------------------------------------------------------------------
    # Persistence helpers
    # -------------------------------------------------------------------------

    async def _load_state(
        self, conn: asyncpg.Connection, workflow_id: UUID
    ) -> WorkflowState | None:
        wf = await conn.fetchrow(
            "SELECT id, kind, status, pattern, current_step FROM workflows WHERE id = $1",
            workflow_id,
        )
        if wf is None:
            return None
        rows = await conn.fetch(
            "SELECT * FROM actions WHERE workflow_id = $1 ORDER BY sequence",
            workflow_id,
        )
        return WorkflowState(
            workflow_id=workflow_id,
            kind=wf["kind"],
            pattern=wf["pattern"] or "",
            current_step=wf["current_step"] or 0,
            actions=[ActionRow.from_record(r) for r in rows],
        )

    def _find_pending(self, state: WorkflowState) -> ActionRow | None:
        """Return the most recent pausing step that hasn't completed, or None."""
        for action in reversed(state.actions):
            if action.step_kind in _PAUSING_KINDS and action.status in (
                "proposed", "approved", "executing"
            ):
                return action
        return None

    def _compute_attempt(
        self, state: WorkflowState, step: Step
    ) -> tuple[int, int | None, UUID | None]:
        """Decide attempt_number, max_attempts, and retry_of for the row about to be written."""
        # CritiqueStep: attempt# of the critique itself; max_attempts on the row only
        # for the *first* attempt. Retry chain via retry_of_action_id.
        latest = state.latest_for_step(state.current_step)
        if latest is None:
            # First attempt for this step.
            max_attempts = (
                step.max_attempts if isinstance(step, CritiqueStep) else None
            )
            return 1, max_attempts, None
        return latest.attempt_number + 1, None, latest.id

    def _collect_critique_feedback(
        self, state: WorkflowState, chain: Chain, step: Step
    ) -> dict[str, Any] | None:
        """Surface the most recent failed critique whose target *is* the step
        about to run. Only relevant when re-entering a step after a critique
        failed and rewound us here. Critique steps themselves never receive
        their own prior result as feedback.
        """
        if isinstance(step, CritiqueStep):
            return None
        for action in reversed(state.actions):
            if action.step_kind != "critique" or not action.critique_result:
                continue
            # Locate the chain step that produced this critique action.
            critique_idx = self._chain_step_index_for_action(state, chain, action)
            if critique_idx is None:
                continue
            critique_step = chain.steps[critique_idx]
            if not isinstance(critique_step, CritiqueStep):
                continue
            if critique_step.critiques_step_index != state.current_step:
                # Most recent critique targets a different step; this step
                # is not being retried because of it.
                return None
            if action.critique_result.get("passed", False):
                return None  # latest critique on this step passed
            return action.critique_result
        return None

    def _chain_step_index_for_action(
        self, state: WorkflowState, chain: Chain, action: ActionRow
    ) -> int | None:
        """Find the chain index that produced the given action by walking the
        retry chain back to its root and matching root.sequence to step index."""
        by_id = {a.id: a for a in state.actions}
        root = action
        while root.retry_of_action_id is not None and root.retry_of_action_id in by_id:
            root = by_id[root.retry_of_action_id]
        idx = root.sequence - 1  # sequences are 1-indexed for first attempts
        if 0 <= idx < len(chain.steps):
            return idx
        return None

    async def _resolve_agent_id(self, conn: asyncpg.Connection, slug: str) -> UUID:
        agent_id = await conn.fetchval(
            "SELECT id FROM agents WHERE slug = $1 AND is_active",
            slug,
        )
        if agent_id is None:
            raise HTTPException(
                status_code=500,
                detail=f"orchestrator: agent slug '{slug}' not found or inactive",
            )
        return agent_id

    async def _insert_action(
        self,
        conn: asyncpg.Connection,
        *,
        workflow_id: UUID,
        agent_id: UUID,
        step: Step,
        proposed_payload: dict[str, Any],
        attempt_number: int,
        max_attempts: int | None,
        retry_of: UUID | None,
    ) -> UUID:
        next_seq = await conn.fetchval(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM actions WHERE workflow_id = $1",
            workflow_id,
        )
        return await conn.fetchval(
            """
            INSERT INTO actions
                (workflow_id, agent_id, sequence, action_type, status,
                 summary, proposed_payload, risk_level,
                 step_kind, attempt_number, max_attempts, retry_of_action_id)
            VALUES ($1, $2, $3, $4, 'proposed', $5, $6, $7, $8, $9, $10, $11)
            RETURNING id
            """,
            workflow_id,
            agent_id,
            next_seq,
            step.action_type,
            step.summary,
            proposed_payload,
            step.risk_level,
            step.step_kind.value,
            attempt_number,
            max_attempts,
            retry_of,
        )

    async def _set_current_step(
        self, conn: asyncpg.Connection, workflow_id: UUID, step_index: int
    ) -> None:
        await conn.execute(
            "UPDATE workflows SET current_step = $1 WHERE id = $2",
            step_index,
            workflow_id,
        )

    async def _mark_completed(self, conn: asyncpg.Connection, workflow_id: UUID) -> None:
        await conn.execute(
            "UPDATE workflows SET status = 'completed', completed_at = now() WHERE id = $1",
            workflow_id,
        )
        await audit.write_audit_event(
            conn,
            "workflow.completed",
            workflow_id=workflow_id,
            actor="orchestrator",
            payload={},
        )

    async def _mark_workflow_failed(
        self, conn: asyncpg.Connection, workflow_id: UUID, error: str
    ) -> None:
        await conn.execute(
            "UPDATE workflows SET status = 'failed', error = $1, completed_at = now() WHERE id = $2",
            error,
            workflow_id,
        )
        await audit.write_audit_event(
            conn,
            "workflow.failed",
            workflow_id=workflow_id,
            actor="orchestrator",
            payload={"error": error},
        )


# Module-level singleton — orchestrator is stateless, so one instance is fine.
orchestrator = PromptChainOrchestrator()
