"""Per-call telemetry for OpenAI chat completions.

The instrumented `call_openai_chat` wrapper (see `app/integrations/openai_client.py`)
captures every request/response and writes a row to `llm_calls`. Callers thread
agent/workflow/purpose context in via the `with_llm_context` context manager so
log rows are attributable to the agent and workflow that made the call.

Streaming callers (see `app/services/chat.py`) cannot use the non-streaming
wrapper, but they call `write_llm_call` directly with the assembled request and
response after each streaming round-trip completes.
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from app.db import get_pool

logger = logging.getLogger(__name__)


@dataclass
class LlmCallContext:
    agent_slug: str | None = None
    workflow_id: UUID | None = None
    thread_id: UUID | None = None
    purpose: str | None = None


_ctx: ContextVar[LlmCallContext] = ContextVar("llm_call_context", default=LlmCallContext())


def current_context() -> LlmCallContext:
    return _ctx.get()


@contextmanager
def with_llm_context(
    *,
    agent_slug: str | None = None,
    workflow_id: UUID | None = None,
    thread_id: UUID | None = None,
    purpose: str | None = None,
):
    """Set per-call telemetry context for nested OpenAI calls.

    Nested calls override only the fields they set — unset fields inherit from
    the outer scope.
    """
    outer = _ctx.get()
    merged = LlmCallContext(
        agent_slug=agent_slug if agent_slug is not None else outer.agent_slug,
        workflow_id=workflow_id if workflow_id is not None else outer.workflow_id,
        thread_id=thread_id if thread_id is not None else outer.thread_id,
        purpose=purpose if purpose is not None else outer.purpose,
    )
    token = _ctx.set(merged)
    try:
        yield merged
    finally:
        _ctx.reset(token)


async def write_llm_call(
    *,
    started_at: datetime,
    ended_at: datetime,
    latency_ms: int,
    model: str,
    request: dict[str, Any],
    response: dict[str, Any] | None,
    status: str,
    error: str | None = None,
    streamed: bool = False,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    agent_slug: str | None = None,
    workflow_id: UUID | None = None,
    thread_id: UUID | None = None,
    purpose: str | None = None,
    provider: str = "openai",
) -> None:
    """Insert one llm_calls row. Caller supplies context overrides explicitly
    when not already on the context var."""
    ctx = _ctx.get()
    agent_slug = agent_slug if agent_slug is not None else ctx.agent_slug
    workflow_id = workflow_id if workflow_id is not None else ctx.workflow_id
    thread_id = thread_id if thread_id is not None else ctx.thread_id
    purpose = purpose if purpose is not None else ctx.purpose

    try:
        pool = await get_pool()
        await pool.execute(
            """
            INSERT INTO llm_calls (
                started_at, ended_at, latency_ms, provider, model,
                agent_slug, workflow_id, thread_id, purpose,
                status, error, streamed,
                request, response,
                prompt_tokens, completion_tokens, total_tokens
            ) VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8, $9,
                $10, $11, $12,
                $13::jsonb, $14::jsonb,
                $15, $16, $17
            )
            """,
            started_at, ended_at, latency_ms, provider, model,
            agent_slug, workflow_id, thread_id, purpose,
            status, error, streamed,
            json.dumps(request, default=str),
            json.dumps(response, default=str) if response is not None else None,
            prompt_tokens, completion_tokens, total_tokens,
        )
    except Exception:
        logger.exception("Failed to write llm_calls row (model=%s, purpose=%s)", model, purpose)


def fire_and_forget_write(**kwargs: Any) -> asyncio.Task:
    """Schedule a write without awaiting it. Errors are logged inside the task."""
    return asyncio.create_task(write_llm_call(**kwargs))
