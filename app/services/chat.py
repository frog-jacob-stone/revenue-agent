"""Streaming chat service.

Drives the OpenAI tool-call loop for a conversational agent and yields
events suitable for SSE:

- `delta`              — assistant text tokens as they stream from the LLM
- `tool_call_started`  — model decided to call a tool
- `workflow_started`   — a tool spawned a LangGraph workflow
- `workflow_event`     — orchestrator audit_log event for the running workflow
- `tool_call_completed`— tool returned (or errored)
- `done`               — final answer ready
- `error`              — terminal failure

The events surfaced by `workflow_started` / `workflow_event` originate
from `ToolContext.progress` (a ProgressEmitter). Tools that spawn
workflows forward audit_log events into it; this generator drains the
emitter concurrently with the tool's execution and interleaves its
events into the SSE stream.
"""

import asyncio
import json
import logging
from typing import Any, AsyncIterator
from uuid import UUID

from app.agents.base import ConversationalAgent
from app.agents.registry import AGENTS_BY_SLUG
from app.integrations.openai_client import get_client
from app.tools.base import ProgressEmitter

logger = logging.getLogger(__name__)


def _get_agent(slug: str) -> ConversationalAgent:
    cls = AGENTS_BY_SLUG.get(slug)
    if cls is None or not issubclass(cls, ConversationalAgent):
        raise ValueError(f"Chat not supported for agent '{slug}'")
    return cls(
        agent_id=UUID(int=0),
        config=dict(cls.default_config),
        allowed_tools=list(cls.allowed_tools),
    )


def _summarize_result(result: Any) -> str:
    """Compact representation of a tool result for the activity log."""
    if isinstance(result, dict):
        if "error" in result:
            return f"error: {result['error']}"
        keys = list(result.keys())[:4]
        return "{" + ", ".join(keys) + ("…}" if len(result) > 4 else "}")
    s = str(result)
    return s if len(s) <= 80 else s[:77] + "…"


async def _execute_streaming(
    agent: ConversationalAgent,
    name: str,
    args: dict[str, Any],
) -> AsyncIterator[dict[str, Any]]:
    """Run a tool and yield progress events as they happen, then the final result.

    Last event yielded is `{"_result": <tool_result>}` — the caller pulls
    that off the end to learn what to feed back to the LLM.
    """
    progress = ProgressEmitter()
    result_holder: dict[str, Any] = {}

    async def run():
        try:
            result_holder["value"] = await agent.execute_tool(name, args, progress=progress)
        except Exception as exc:
            logger.exception("Tool %s failed", name)
            result_holder["value"] = {"error": str(exc)}
        finally:
            progress.close()

    runner_task = asyncio.create_task(run())
    async for evt in progress.drain():
        yield evt
    await runner_task
    yield {"_result": result_holder["value"]}


async def agent_chat_stream(
    agent_slug: str,
    messages: list[dict],
) -> AsyncIterator[dict[str, Any]]:
    """Stream events for one chat turn through the OpenAI tool-call loop."""
    agent = _get_agent(agent_slug)
    client = get_client()

    msg_list: list[dict] = [{"role": "system", "content": agent.get_system_prompt()}] + list(messages)
    tools = agent.get_tools()
    last_tool_used: str | None = None
    final_answer: str = ""

    while True:
        model = agent.config.get("model", "gpt-4o-mini")

        # Stream the LLM response so we can emit token deltas and assemble
        # any tool-call deltas as they arrive.
        stream = await client.chat.completions.create(
            model=model,
            messages=msg_list,
            stream=True,
            **({"tools": tools} if tools else {}),
        )

        text_buf: list[str] = []
        tool_calls_buf: dict[int, dict[str, Any]] = {}  # index -> partial tool_call
        finish_reason: str | None = None

        async for chunk in stream:
            choice = chunk.choices[0]
            delta = choice.delta

            if delta.content:
                text_buf.append(delta.content)
                yield {"type": "delta", "text": delta.content}

            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    slot = tool_calls_buf.setdefault(
                        idx, {"id": "", "name": "", "arguments": ""}
                    )
                    if tc_delta.id:
                        slot["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            slot["name"] += tc_delta.function.name
                        if tc_delta.function.arguments:
                            slot["arguments"] += tc_delta.function.arguments

            if choice.finish_reason:
                finish_reason = choice.finish_reason

        if finish_reason != "tool_calls":
            final_answer = "".join(text_buf)
            break

        # Rebuild the assistant message so the next LLM call has full context.
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(text_buf) or None,
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]},
                }
                for tc in tool_calls_buf.values()
            ],
        }
        msg_list.append(assistant_msg)

        for tc in tool_calls_buf.values():
            name = tc["name"]
            try:
                args = json.loads(tc["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            last_tool_used = name
            yield {"type": "tool_call_started", "name": name, "args": args}

            tool_result: Any = None
            async for evt in _execute_streaming(agent, name, args):
                if "_result" in evt:
                    tool_result = evt["_result"]
                else:
                    yield evt

            ok = not (isinstance(tool_result, dict) and "error" in tool_result)
            yield {
                "type": "tool_call_completed",
                "name": name,
                "ok": ok,
                "result_summary": _summarize_result(tool_result),
            }

            msg_list.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(tool_result, default=str),
            })

    yield {"type": "done", "answer": final_answer, "tool_used": last_tool_used}
