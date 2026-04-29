"""Abstract orchestrator interface.

Kept thin: today there is only PromptChainOrchestrator. If a second pattern
needs different scheduling logic, extract shared concerns here then.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any
from uuid import UUID


class BaseOrchestrator(ABC):
    @abstractmethod
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
        """Create a workflow for the chain registered under `kind` and run it
        until it pauses (checkpoint/execution) or completes.

        Returns the workflow_id.
        """
        ...

    @abstractmethod
    async def resume(self, workflow_id: UUID) -> None:
        """Resume a workflow whose pending action has just been approved.

        Idempotent: safe to call when the workflow has no pending approval
        (it will simply continue from the next step or no-op).
        """
        ...
