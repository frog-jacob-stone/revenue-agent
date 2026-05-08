"""Uniform entry point for invoking an agent from anywhere.

Same surface from a graph node, a sub-agent, or the chat layer. Phase 0
gives a minimal implementation: look up the agent class, build a single-turn
prompt, dispatch to the right provider, wrap with audit events.

Phase 4 extends this for tool-use loops and agent-to-agent messaging.
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
from app.integrations.anthropic_client import call_anthropic
from app.orchestrator_v2 import events
from app.services import audit

logger = logging.getLogger(__name__)


@dataclass
class NodeContext:
    """Lightweight context for v2 nodes.

    Carries identity (workflow + parent linkage) and a DB pool for callers
    that need to read or write rows. Kept small on purpose; richer chain
    context (`StepContext` from v1) is not ported.
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
    AGENT_FAILED on exception.
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
        # Best-effort: instantiate without DB-loaded config since this is a
        # single-turn invocation. Phase 4 will wire full agent loading.
        try:
            instance = agent_cls(agent_id=agent_id, config={})  # type: ignore[arg-type]
            system_prompt = instance.get_system_prompt()
        except Exception:
            system_prompt = getattr(agent_cls, "system_prompt", "") or ""
    else:
        system_prompt = getattr(agent_cls, "system_prompt", "") or ""

    full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt

    async with pool.acquire() as conn:
        await audit.write_audit_event(
            conn,
            events.AGENT_INVOKED,
            workflow_id=workflow_id,
            agent_id=agent_id,
            actor=f"orchestrator_v2:{slug}",
            payload={"slug": slug, "max_tokens": max_tokens},
        )

    try:
        text = await call_anthropic(
            full_prompt,
            model=agent_cls.model,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        async with pool.acquire() as conn:
            await audit.write_audit_event(
                conn,
                events.AGENT_FAILED,
                workflow_id=workflow_id,
                agent_id=agent_id,
                actor=f"orchestrator_v2:{slug}",
                payload={"error": str(exc)},
            )
        raise

    async with pool.acquire() as conn:
        await audit.write_audit_event(
            conn,
            events.AGENT_COMPLETED,
            workflow_id=workflow_id,
            agent_id=agent_id,
            actor=f"orchestrator_v2:{slug}",
            payload={"slug": slug, "chars": len(text)},
        )
    return {"text": text}
