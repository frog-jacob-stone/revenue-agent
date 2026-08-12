"""Executor base types.

An executor is the function that actually performs the write described by
an `AwaitingApproval` — invoked by the approval-grant handler, not by any
LLM. Receives the (possibly edited) payload from the inbox.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from uuid import UUID

import asyncpg


@dataclass
class ExecutorContext:
    """Context passed to an executor at invocation time.

    Distinct from `ToolContext` because executors have no agent identity
    and no LLM streaming — they run in a background task off the approval
    grant request.
    """
    approval_id: UUID
    approved_by: str
    pool: asyncpg.Pool


ExecutorCallable = Callable[[ExecutorContext, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ExecutorDefinition:
    """One executor.

    `name` is what the proposing tool puts in `AwaitingApproval.executor`
    and what's stored on the approval row. `description` is for audit /
    inbox display only; executors have no schema exposed to any LLM.
    """
    name: str
    description: str
    execute: ExecutorCallable
