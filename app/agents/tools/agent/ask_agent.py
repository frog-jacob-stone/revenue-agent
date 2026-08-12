"""Agent-to-agent delegation tool.

`ask_agent` lets one agent delegate a task to another and receive its answer.
Both the outgoing prompt and the incoming reply are recorded in `agent_messages`
under a shared `thread_id`. `run_agent_task` handles both ReAct (when the
target has tools) and single-turn (when it doesn't) internally.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from app.db import get_pool
from app.services import agent_messages
from app.agents.tools.base import Blocked, Done, ToolContext, ToolDefinition, ToolReturn


async def _ask_agent(
    ctx: ToolContext,
    *,
    target_slug: str,
    prompt: str,
    thread_id: str | None = None,
    **_: Any,
) -> ToolReturn:
    # Lazy imports avoid a cycle:
    # app/orchestrator/__init__.py → agent_invoke → app.agents.registry →
    # app.agents.revenue_ops_agent → app.agents.tools.agent.ask_agent (this module).
    from app.agents.registry import AGENTS_BY_SLUG
    from app.orchestrator.agent_invoke import NodeContext, run_agent_task

    # Structural delegation allowlist (ADR-0003). The caller declares its
    # `available_agents` set on the class; this tool enforces it. The
    # LLM cannot escape the constraint by hallucinating a target slug.
    caller_cls = AGENTS_BY_SLUG.get(ctx.agent_slug or "")
    if caller_cls is None:
        return Blocked(reason=f"Unknown caller agent '{ctx.agent_slug}'.")
    allowed_slugs = {cls.slug for cls in caller_cls.available_agents}
    if target_slug not in allowed_slugs:
        allowed = ", ".join(sorted(allowed_slugs)) or "(none)"
        return Blocked(
            reason=(
                f"Agent '{ctx.agent_slug}' is not permitted to delegate to "
                f"'{target_slug}'. Allowed targets: {allowed}."
            ),
        )

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

    # 2. Dispatch — run_agent_task handles ReAct and single-turn internally.
    node_ctx = NodeContext(workflow_id=workflow_id) if workflow_id else None
    response = await run_agent_task(
        target_slug, prompt, node_ctx, progress=ctx.progress
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

    return Done({"answer": answer, "thread_id": str(thread_uuid)})


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
