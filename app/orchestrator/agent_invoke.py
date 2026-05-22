"""Uniform entry point for invoking an agent from anywhere.

Two invocation modes:

`invoke_agent` — single-turn LLM call. Used from graph nodes where the step
    sequence is prescribed and no tool loop is needed.

`run_agent_task` — ReAct loop. Used when a domain agent should autonomously
    decide which tools to call and in what order. Drives the loop until the
    model returns a final answer or `max_iterations` is exhausted.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from app.agents.base import BaseAgent, ConversationalAgent
from app.agents.registry import AGENTS
from app.db import get_pool
from app.integrations.llm import Attribution, dispatch
from app.orchestrator import events
from app.services import audit
from app.tools.base import ProgressEmitter, ToolContext

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
        response = await dispatch(
            model=agent_cls.model,
            messages=messages,
            attribution=Attribution(
                agent_slug=slug,
                purpose=f"agent:{slug}",
                workflow_id=workflow_id,
            ),
            max_tokens=max_tokens,
        )
        text = response.text
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


def _summarize(result: Any) -> str:
    if isinstance(result, dict):
        if "error" in result:
            return f"error: {result['error']}"
        keys = list(result.keys())[:4]
        return "{" + ", ".join(keys) + ("…}" if len(result) > 4 else "}")
    s = str(result)
    return s if len(s) <= 80 else s[:77] + "…"


async def run_agent_task(
    slug: str,
    task: str,
    ctx: NodeContext | None = None,
    *,
    max_iterations: int = 10,
    progress: ProgressEmitter | None = None,
) -> dict[str, Any]:
    """Run a domain agent autonomously using a ReAct tool-use loop.

    Drives tool calls until the model returns a final answer (finish_reason
    stop with no tool_calls) or `max_iterations` is exhausted. Writes
    AGENT_INVOKED, AGENT_COMPLETED, and AGENT_FAILED audit events bracketing
    the entire task (not per iteration).

    Falls back to a single-turn dispatch when the agent has no allowed_tools.
    """
    agent_cls = _agent_class_for_slug(slug)
    allowed_tools = list(getattr(agent_cls, "allowed_tools", ()) or ())

    if not allowed_tools:
        return await invoke_agent(slug, {"prompt": task}, ctx)

    pool = (ctx.pool if ctx else None) or await get_pool()
    agent_id = await _agent_id_for_slug(pool, slug)
    workflow_id = ctx.workflow_id if ctx else None

    system_prompt: str = ""
    if issubclass(agent_cls, ConversationalAgent):
        try:
            instance = agent_cls(agent_id=agent_id)  # type: ignore[arg-type]
            system_prompt = instance.get_system_prompt()
        except Exception:
            system_prompt = getattr(agent_cls, "system_prompt", "") or ""
    else:
        system_prompt = getattr(agent_cls, "system_prompt", "") or ""

    tool_schemas = [t.as_openai_schema() for t in allowed_tools]
    tool_by_name = {t.name: t for t in allowed_tools}
    tool_ctx = ToolContext(
        agent_id=agent_id or uuid4(),
        agent_slug=slug,
        workflow_id=workflow_id,
    )

    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": task})

    async with pool.acquire() as conn:
        await audit.write_audit_event(
            conn,
            events.AGENT_INVOKED,
            workflow_id=workflow_id,
            agent_id=agent_id,
            actor=f"orchestrator:{slug}",
            payload={"slug": slug, "task_chars": len(task)},
        )

    text = ""
    try:
        for _ in range(max_iterations):
            response = await dispatch(
                model=agent_cls.model,
                messages=messages,
                attribution=Attribution(
                    agent_slug=slug,
                    purpose=f"agent:{slug}",
                    workflow_id=workflow_id,
                ),
                tools=tool_schemas,
            )

            if not response.tool_calls:
                text = response.text
                break

            # Append the assistant turn with its tool calls.
            messages.append(
                {
                    "role": "assistant",
                    "content": response.text or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": tc.arguments,
                            },
                        }
                        for tc in response.tool_calls
                    ],
                }
            )

            # Execute each tool call and append results.
            from app.orchestrator.dispatch import dispatch_tool

            for tc in response.tool_calls:
                tool = tool_by_name.get(tc.name)
                if tool is None:
                    result: Any = {"error": f"Unknown tool '{tc.name}'."}
                    args: dict[str, Any] = {}
                else:
                    try:
                        args = json.loads(tc.arguments) if tc.arguments else {}
                    except json.JSONDecodeError:
                        args = {}
                    if progress:
                        progress.emit({
                            "type": "agent_task_tool_started",
                            "agent_slug": slug,
                            "name": tc.name,
                            "args": args,
                        })
                    try:
                        result = await dispatch_tool(tool, tool_ctx, args)
                    except Exception as exc:
                        result = {"error": str(exc)}
                if progress:
                    progress.emit({
                        "type": "agent_task_tool_completed",
                        "agent_slug": slug,
                        "name": tc.name,
                        "ok": not (isinstance(result, dict) and "error" in result),
                        "result_summary": _summarize(result),
                    })
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result),
                    }
                )
        else:
            raise RuntimeError(
                f"Agent '{slug}' exceeded maximum iterations ({max_iterations})."
            )
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
