"""Agent-to-agent delegation tool.

`ask_agent` lets one agent delegate a question to another and receive a
single-turn answer. Both the outgoing prompt and the incoming reply are
recorded in `agent_messages` under a shared `thread_id`.

Phase 4 of the LangGraph multi-agent rearchitecture (see
.agent/plans/8.path-b-phase-4-multi-agent.md). The tool wraps `invoke_agent`
(single-turn Anthropic call); native Anthropic tool-use loops are out of
scope for this phase.

Loop safety: callers that want multi-step delegation must bound their own
iterations. There is no framework-level guard against an unbounded
ask_agent → ask_agent → ... chain.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from app.db import get_pool
from app.orchestrator_v2.agent_invoke import NodeContext, invoke_agent
from app.services import agent_messages
from app.tools.base import ToolContext, ToolDefinition


async def _ask_agent(
    ctx: ToolContext,
    *,
    target_slug: str,
    prompt: str,
    thread_id: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    pool = await get_pool()
    thread_uuid = UUID(thread_id) if thread_id else uuid4()
    workflow_id = ctx.workflow_id

    # 1. Record the outgoing question.
    await agent_messages.send_message(
        pool,
        from_agent_slug=ctx.agent_slug,
        to_agent_slug=target_slug,
        content=prompt,
        thread_id=thread_uuid,
        workflow_id=workflow_id,
    )

    # 2. Get the answer (single-turn).
    node_ctx = NodeContext(workflow_id=workflow_id) if workflow_id else None
    response = await invoke_agent(
        target_slug,
        {"prompt": prompt, "max_tokens": 800},
        node_ctx,
    )
    answer = response["text"]

    # 3. Record the answer.
    await agent_messages.send_message(
        pool,
        from_agent_slug=target_slug,
        to_agent_slug=ctx.agent_slug,
        content=answer,
        thread_id=thread_uuid,
        workflow_id=workflow_id,
    )

    return {"answer": answer, "thread_id": str(thread_uuid)}


ASK_AGENT = ToolDefinition(
    name="ask_agent",
    description=(
        "Ask another agent a question. Returns the agent's reply as text plus "
        "the thread_id used to record both messages. Pass an existing thread_id "
        "to continue a conversation; omit it to start a new thread."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "target_slug": {
                "type": "string",
                "description": "Slug of the agent to ask (must exist in the agents registry).",
            },
            "prompt": {
                "type": "string",
                "description": "The question or request to send to the target agent.",
            },
            "thread_id": {
                "type": "string",
                "description": (
                    "Optional thread UUID to continue a prior exchange. "
                    "Omit to start a new thread."
                ),
            },
        },
        "required": ["target_slug", "prompt"],
    },
    execute=_ask_agent,
)
