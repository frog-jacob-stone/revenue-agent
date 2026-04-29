"""Runtime state passed to step handlers and orchestrator internals.

State is reconstructed from the `actions` rows on every resume — they are the
source of truth. WorkflowState is a runtime view, never persisted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import asyncpg


@dataclass
class ActionRow:
    """Lightweight in-memory copy of an actions row."""

    id: UUID
    sequence: int
    step_kind: str | None
    status: str
    proposed_payload: dict[str, Any]
    executed_payload: dict[str, Any] | None
    result: dict[str, Any] | None
    critique_result: dict[str, Any] | None
    parent_action_id: UUID | None
    retry_of_action_id: UUID | None
    attempt_number: int
    max_attempts: int | None
    error: str | None

    @classmethod
    def from_record(cls, row: asyncpg.Record) -> "ActionRow":
        return cls(
            id=row["id"],
            sequence=row["sequence"],
            step_kind=row["step_kind"],
            status=row["status"],
            proposed_payload=row["proposed_payload"] or {},
            executed_payload=row["executed_payload"],
            result=row["result"],
            critique_result=row["critique_result"],
            parent_action_id=row["parent_action_id"],
            retry_of_action_id=row["retry_of_action_id"],
            attempt_number=row["attempt_number"],
            max_attempts=row["max_attempts"],
            error=row["error"],
        )


@dataclass
class WorkflowState:
    """Runtime view of a workflow's progress, hydrated from actions on each resume."""

    workflow_id: UUID
    kind: str
    pattern: str
    current_step: int  # next chain step index to execute (0-indexed)
    actions: list[ActionRow] = field(default_factory=list)

    def latest_for_step(self, step_index: int) -> ActionRow | None:
        """Most recent action for the given chain step index, or None."""
        # Step index aligns with the *root* (first attempt) sequence: when we write
        # the first attempt of step N, it gets sequence = N + 1 (sequences are
        # 1-indexed). Retries chain back via retry_of_action_id; their root is the
        # first attempt of the same step.
        root_sequence = step_index + 1
        candidates = [a for a in self._chain_for_step(step_index, root_sequence)]
        if not candidates:
            return None
        return max(candidates, key=lambda a: a.attempt_number)

    def attempts_for_step(self, step_index: int) -> int:
        """How many times the given chain step has been attempted."""
        latest = self.latest_for_step(step_index)
        return latest.attempt_number if latest else 0

    def _chain_for_step(self, step_index: int, root_sequence: int) -> list[ActionRow]:
        """All attempts (including retries) of the step whose root has the given sequence."""
        by_id = {a.id: a for a in self.actions}
        out: list[ActionRow] = []
        for action in self.actions:
            root = action
            while root.retry_of_action_id is not None and root.retry_of_action_id in by_id:
                root = by_id[root.retry_of_action_id]
            if root.sequence == root_sequence:
                out.append(action)
        return out


@dataclass
class StepContext:
    """Context handed to each step's handler."""

    workflow_id: UUID
    workflow_kind: str
    step_index: int
    attempt_number: int
    state: WorkflowState
    conn: asyncpg.Connection
    # Set when this step is being retried because a downstream critique failed.
    # Handlers can use this to inform a revised draft.
    critique_feedback: dict[str, Any] | None = None
    # For execution steps on resume: what the human approved (may differ from draft).
    executed_payload: dict[str, Any] | None = None
