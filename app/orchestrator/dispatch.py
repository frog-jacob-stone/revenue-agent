"""Tool dispatch runtime (ADR-0002).

Every LLM-driven tool call goes through `dispatch_tool`. It:
  1. Writes a TOOL_CALLED audit event
  2. Invokes the tool's `execute` with the provided arguments
  3. Pattern-matches the return shape:
       Done(payload)        -> TOOL_COMPLETED audit; returns payload to caller
       AwaitingApproval(...) -> creates approval row (APPROVAL_REQUESTED
                                 audit fires inside `create_pending_for_tool`);
                                 returns {"status": "awaiting_approval", ...}
       Blocked(...)         -> TOOL_BLOCKED audit; returns {"status": "blocked", ...}
  4. On exception: TOOL_FAILED audit, re-raises

The caller (an LLM-driven loop in `run_agent_task` or
`Agent.execute_tool`) sees a plain dict suitable for
JSON-encoding into a tool result message.
"""
from __future__ import annotations

import logging
from typing import Any

from app.db import get_pool
from app.orchestrator import events
from app.services import approvals as approvals_service
from app.services import audit
from app.agents.tools.base import (
    AwaitingApproval,
    Blocked,
    Done,
    ToolContext,
    ToolDefinition,
)

logger = logging.getLogger(__name__)


async def dispatch_tool(
    tool: ToolDefinition,
    ctx: ToolContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    pool = await get_pool()

    async with pool.acquire() as conn:
        await audit.write_audit_event(
            conn,
            events.TOOL_CALLED,
            actor=f"agent:{ctx.agent_slug}",
            payload={"tool": tool.name, "arguments": arguments},
        )

    try:
        result = await tool.execute(ctx, **arguments)
    except Exception as exc:
        async with pool.acquire() as conn:
            await audit.write_audit_event(
                conn,
                events.TOOL_FAILED,
                actor=f"agent:{ctx.agent_slug}",
                payload={"tool": tool.name, "error": str(exc)},
            )
        raise

    match result:
        case Done(payload=payload):
            async with pool.acquire() as conn:
                await audit.write_audit_event(
                    conn,
                    events.TOOL_COMPLETED,
                    actor=f"agent:{ctx.agent_slug}",
                    payload={"tool": tool.name},
                )
            return payload

        case AwaitingApproval(
            executor=executor,
            payload=payload,
            summary=summary,
            action_type=action_type,
            reasoning=reasoning,
            risk_level=risk_level,
        ):
            async with pool.acquire() as conn:
                async with conn.transaction():
                    row = await approvals_service.create_pending_for_tool(
                        conn,
                        agent_slug=ctx.agent_slug,
                        executor=executor,
                        action_type=action_type,
                        proposed_payload=payload,
                        summary=summary,
                        reasoning=reasoning,
                        risk_level=risk_level,
                    )
            return {
                "status": "awaiting_approval",
                "approval_id": str(row["id"]),
                "summary": summary,
            }

        case Blocked(reason=reason, hint=hint):
            async with pool.acquire() as conn:
                await audit.write_audit_event(
                    conn,
                    events.TOOL_BLOCKED,
                    actor=f"agent:{ctx.agent_slug}",
                    payload={"tool": tool.name, "reason": reason, "hint": hint or {}},
                )
            return {
                "status": "blocked",
                "reason": reason,
                "hint": hint or {},
            }

        case _:
            raise TypeError(
                f"Tool '{tool.name}' returned {type(result).__name__}; "
                f"must return one of Done | AwaitingApproval | Blocked"
            )
