"""Uniform entry point for invoking an agent from anywhere.

Same surface from a graph node, a sub-agent, or the chat layer. Looks up the
agent class, builds a single-turn prompt, dispatches to OpenAI via the
instrumented `call_openai_chat` wrapper, and wraps the call with audit events.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

from app.agents.base import BaseAgent, ConversationalAgent
from app.agents.registry import AGENTS
from app.db import get_pool
from app.integrations.openai_client import call_openai_chat
from app.orchestrator import events
from app.services import audit
from app.services.llm_logging import with_llm_context

logger = logging.getLogger(__name__)


@dataclass
class NodeContext:
    """Lightweight context for graph nodes.

    Carries identity (workflow + parent linkage) and a DB pool for callers
    that need to read or write rows.
    """

    workflow_id: UUID
    parent_workflow_id: UUID | None = None
    pool: asyncpg.Pool | None = None


def _agent_class_for_slug(slug: str) -> type[BaseAgent]:
    for cls in AGENTS:
        if getattr(cls, "slug", None) == slug:
            return cls
    raise KeyError(f"agent slug not found in registry: {slug}")


async def _agent_id_for_slug(pool: asyncpg.Pool, slug: str) -> UUID | None:
    return await pool.fetchval("SELECT id FROM agents WHERE slug = $1", slug)


async def invoke_agent(
    slug: str,
    input: dict[str, Any],
    ctx: NodeContext | None = None,
) -> dict[str, Any]:
    """Invoke an agent and return its response.

    `input` shape:
      {
        "prompt": str,        # required — the user-message content
        "max_tokens": int,    # optional, default 1000
      }

    Returns:
      { "text": str }

    Audit events: AGENT_INVOKED before the call, AGENT_COMPLETED on success,
    AGENT_FAILED on exception. LLM telemetry (full request/response, tokens,
    latency) is written separately to `llm_calls` by the wrapper.
    """
    agent_cls = _agent_class_for_slug(slug)
    pool = (ctx.pool if ctx else None) or await get_pool()
    agent_id = await _agent_id_for_slug(pool, slug)
    workflow_id = ctx.workflow_id if ctx else None
    prompt: str = input["prompt"]
    max_tokens = int(input.get("max_tokens", 1000))

    # Compose system prompt + user content. ConversationalAgents have
    # get_system_prompt(); BaseAgents may have a class-level system_prompt.
    system_prompt = ""
    if issubclass(agent_cls, ConversationalAgent):
        try:
            instance = agent_cls(agent_id=agent_id, config={})  # type: ignore[arg-type]
            system_prompt = instance.get_system_prompt()
        except Exception:
            system_prompt = getattr(agent_cls, "system_prompt", "") or ""
    else:
        system_prompt = getattr(agent_cls, "system_prompt", "") or ""

    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    async with pool.acquire() as conn:
        await audit.write_audit_event(
            conn,
            events.AGENT_INVOKED,
            workflow_id=workflow_id,
            agent_id=agent_id,
            actor=f"orchestrator:{slug}",
            payload={"slug": slug, "max_tokens": max_tokens},
        )

    try:
        with with_llm_context(
            agent_slug=slug,
            workflow_id=workflow_id,
            purpose=f"agent:{slug}",
        ):
            completion = await call_openai_chat(
                model=agent_cls.model,
                messages=messages,
                max_tokens=max_tokens,
            )
        text = completion.choices[0].message.content or ""
    except Exception as exc:
        async with pool.acquire() as conn:
            await audit.write_audit_event(
                conn,
                events.AGENT_FAILED,
                workflow_id=workflow_id,
                agent_id=agent_id,
                actor=f"orchestrator:{slug}",
                payload={"error": str(exc)},
            )
        raise

    async with pool.acquire() as conn:
        await audit.write_audit_event(
            conn,
            events.AGENT_COMPLETED,
            workflow_id=workflow_id,
            agent_id=agent_id,
            actor=f"orchestrator:{slug}",
            payload={"slug": slug, "chars": len(text)},
        )
    return {"text": text}
