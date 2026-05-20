"""Telemetry persistence for LLM calls.

`write_llm_call` inserts one row into `llm_calls`. The dispatcher in
`app/integrations/llm.py` is the only caller — every LLM call in the system
flows through there, and the dispatcher writes the row from inside its
`_emit_row` helper with `Attribution` fields attached.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from app.db import get_pool

logger = logging.getLogger(__name__)


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
    """Insert one `llm_calls` row. Failures are logged and swallowed —
    telemetry writes never mask an otherwise-successful (or failed) LLM call.
    """
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
