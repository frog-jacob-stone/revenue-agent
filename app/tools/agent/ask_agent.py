"""Agent-to-agent delegation tool.

`ask_agent` lets one agent delegate a task to another and receive its answer.
Both the outgoing prompt and the incoming reply are recorded in `agent_messages`
under a shared `thread_id`.

Routing: if the target agent has `allowed_tools`, the call drives a ReAct loop
via `run_agent_task` — the agent decides which tools to call and returns a
final answer. If the target has no tools, falls back to a single-turn
`invoke_agent` call.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from app.db import get_pool
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
    # Lazy imports avoid a cycle:
    # app/orchestrator/__init__.py → agent_invoke → app.agents.registry →
    # app.agents.revenue_ops_agent → app.tools.agent.ask_agent (this module).
    from app.agents.registry import AGENTS_BY_SLUG
    from app.orchestrator.agent_invoke import NodeContext, invoke_agent, run_agent_task

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

    # 2. Dispatch: ReAct loop for agents with tools, single-turn otherwise.
    node_ctx = NodeContext(workflow_id=workflow_id) if workflow_id else None
    agent_cls = AGENTS_BY_SLUG.get(target_slug)
    if agent_cls and getattr(agent_cls, "allowed_tools", ()):
        response = await run_agent_task(
            target_slug, prompt, node_ctx, progress=ctx.progress
        )
    else:
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
