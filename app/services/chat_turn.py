"""One chat turn — end to end.

The chat surface has a single front-door conversational agent
(`FRONT_DOOR_SLUG`). A turn begins when the user posts a message and ends
when the OpenAI tool-call loop terminates and the final assistant row is
persisted to `chat_messages`.

This module absorbs three concepts that used to live in three files:
  - The OpenAI tool-call loop (was `chat.py::agent_chat_stream`) — private
    generator `_stream_llm_turn`.
  - The detached turn runtime (was `chat_runtime.py::TurnRuntime`) — public.
  - The turn-lifecycle DB ops (was scattered in `chat_sessions.py`) — private.

The runtime runs in a detached `asyncio.Task` that survives the originating
request being cancelled. If the user navigates away mid-stream, the loop
keeps going and the final state is persisted; the user comes back, fetches
messages, and sees the completed turn.

At most one SSE subscriber per turn. A second client on the same chat sees
`status='streaming'` and polls until done.

Public surface:
  - `FRONT_DOOR_SLUG` — module-level constant; the only agent users chat with.
  - `start_turn(pool, session_id, content)` — append user message + placeholder,
    load history, spawn the runtime task, return the `TurnRuntime` for SSE
    subscription.
  - `get_active(turn_id)` — registry lookup; None if turn has completed.
  - `TurnRuntime.subscribe()` / `.unsubscribe()` — single-subscriber SSE bridge.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator
from uuid import UUID, uuid4

import asyncpg

from app.agents.base import Agent
from app.agents.registry import AGENTS_BY_SLUG
from app.agents.chief_of_staff_agent import ChiefOfStaffAgent
from app.integrations.llm import Attribution, LlmResponse, StreamDelta, dispatch_stream
from app.orchestrator import events
from app.services import audit
from app.services.activity_builder import ActivityState, apply_event
from app.agents.tools.base import ProgressEmitter

logger = logging.getLogger(__name__)

FRONT_DOOR_SLUG: str = ChiefOfStaffAgent.slug


# ── Agent helpers ───────────────────────────────────────────────────────────


def _get_front_door() -> Agent:
    """Instantiate the orchestrator agent users chat with."""
    cls = AGENTS_BY_SLUG.get(FRONT_DOOR_SLUG)
    if cls is None:
        raise RuntimeError(
            f"Orchestrator agent '{FRONT_DOOR_SLUG}' is not registered"
        )
    return cls(agent_id=UUID(int=0))


def _summarize_result(result: Any) -> str:
    """Compact representation of a tool result for the activity log."""
    if isinstance(result, dict):
        if "error" in result:
            return f"error: {result['error']}"
        keys = list(result.keys())[:4]
        return "{" + ", ".join(keys) + ("…}" if len(result) > 4 else "}")
    s = str(result)
    return s if len(s) <= 80 else s[:77] + "…"


# ── OpenAI tool-call loop (private) ─────────────────────────────────────────


async def _execute_tool_streaming(
    agent: Agent,
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


async def _stream_llm_turn(
    messages: list[dict[str, Any]],
) -> AsyncIterator[dict[str, Any]]:
    """Drive the LLM tool-call loop for one chat turn and yield SSE events.

    The system prompt and tool roster are pulled from the orchestrator agent
    (`FRONT_DOOR_SLUG`). No agent selection — there is only one.

    The streaming dispatcher owns provider details and `llm_calls` row writes;
    this function owns only the tool-use loop: assemble each round-trip's
    deltas, decide whether to call tools, thread tool results back into the
    message list, loop.

    Events yielded: delta, tool_call_started, tool_step_started,
    tool_step_completed, tool_call_completed, done. (Tools may also emit
    `agent_task_tool_started` / `agent_task_tool_completed` from nested
    `run_agent_task` calls; those pass through unchanged.)
    """
    agent = _get_front_door()

    msg_list: list[dict] = [{"role": "system", "content": agent.get_system_prompt()}] + list(messages)
    tools = agent.get_tools()
    last_tool_used: str | None = None
    final_answer: str = ""
    attribution = Attribution(agent_slug=FRONT_DOOR_SLUG, purpose="chat")

    while True:
        model = agent.model or "gpt-4o-mini"

        tool_calls_buf: dict[int, dict[str, Any]] = {}
        terminal: LlmResponse | None = None

        async for evt in dispatch_stream(
            model=model,
            messages=msg_list,
            attribution=attribution,
            tools=tools or None,
        ):
            if isinstance(evt, LlmResponse):
                terminal = evt
                continue
            # StreamDelta — either text or a tool-call delta fragment.
            if evt.text:
                yield {"type": "delta", "text": evt.text}
            if evt.tool_call_index is not None:
                slot = tool_calls_buf.setdefault(
                    evt.tool_call_index, {"id": "", "name": "", "arguments": ""}
                )
                if evt.tool_call_id:
                    slot["id"] = evt.tool_call_id
                if evt.tool_call_name_delta:
                    slot["name"] += evt.tool_call_name_delta
                if evt.tool_call_args_delta:
                    slot["arguments"] += evt.tool_call_args_delta

        assert terminal is not None, "dispatch_stream must yield a terminal LlmResponse"
        finish_reason = terminal.finish_reason
        assembled_text = terminal.text

        if finish_reason != "tool_calls":
            final_answer = assembled_text
            break

        # Prefer the dispatcher's reconstructed tool_calls (terminal.tool_calls)
        # so chat_turn doesn't depend on its own buffer reconstruction. The
        # assembled args strings are what OpenAI expects on the round-trip.
        assembled_tool_calls = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": tc.arguments},
            }
            for tc in terminal.tool_calls
        ]

        msg_list.append({
            "role": "assistant",
            "content": assembled_text or None,
            "tool_calls": assembled_tool_calls,
        })

        for tc in terminal.tool_calls:
            name = tc.name
            try:
                args = json.loads(tc.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            last_tool_used = name
            yield {"type": "tool_call_started", "name": name, "args": args}

            tool_result: Any = None
            async for sub_evt in _execute_tool_streaming(agent, name, args):
                if "_result" in sub_evt:
                    tool_result = sub_evt["_result"]
                else:
                    yield sub_evt

            ok = not (isinstance(tool_result, dict) and "error" in tool_result)
            yield {
                "type": "tool_call_completed",
                "name": name,
                "ok": ok,
                "result_summary": _summarize_result(tool_result),
            }

            msg_list.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(tool_result, default=str),
            })

    yield {"type": "done", "answer": final_answer, "tool_used": last_tool_used}


# ── Turn-lifecycle DB ops (private) ─────────────────────────────────────────


def _title_from_user_text(text: str, max_len: int = 60) -> str:
    """Single-line truncated title for sidebar display."""
    s = " ".join(text.strip().split())
    if not s:
        return "New chat"
    if len(s) <= max_len:
        return s
    return s[: max_len - 1].rstrip() + "…"


async def _append_user_message_and_prepare_turn(
    pool: asyncpg.Pool,
    session_id: UUID,
    content: str,
) -> tuple[UUID, int]:
    """In one transaction:
      1. Insert the user message.
      2. If this is the session's first message, set the title.
      3. Mint a turn_id.
      4. Insert the placeholder assistant message (status='streaming').
      5. Bump session.last_message_at, updated_at.
    Returns (turn_id, placeholder_assistant_message_id).
    """
    turn_id = uuid4()
    async with pool.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchval(
                "SELECT count(*) FROM chat_messages WHERE session_id = $1",
                session_id,
            )
            await conn.execute(
                """
                INSERT INTO chat_messages (session_id, role, content, status)
                VALUES ($1, 'user', $2, 'complete')
                """,
                session_id,
                content,
            )
            if existing == 0:
                await conn.execute(
                    "UPDATE chat_sessions SET title = $2 WHERE id = $1",
                    session_id,
                    _title_from_user_text(content),
                )
            placeholder_id = await conn.fetchval(
                """
                INSERT INTO chat_messages
                    (session_id, turn_id, role, content, status)
                VALUES ($1, $2, 'assistant', '', 'streaming')
                RETURNING id
                """,
                session_id,
                turn_id,
            )
            await conn.execute(
                """
                UPDATE chat_sessions
                SET last_message_at = now(), updated_at = now()
                WHERE id = $1
                """,
                session_id,
            )
            await audit.write_audit_event(
                conn,
                events.CHAT_TURN_STARTED,
                actor=f"chat:{session_id}",
                payload={
                    "turn_id": str(turn_id),
                    "session_id": str(session_id),
                    "user_chars": len(content),
                },
            )
    return turn_id, placeholder_id


async def _finalize_assistant_message(
    pool: asyncpg.Pool,
    *,
    turn_id: UUID,
    content: str,
    activity: list[dict[str, Any]],
    status: str,
    tool_used: str | None,
    error: str | None,
) -> None:
    """Mark the placeholder assistant row as complete/failed and update the
    session's last_message_at / updated_at in one transaction."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                UPDATE chat_messages
                SET content = $2,
                    activity = $3,
                    status = $4,
                    tool_used = $5,
                    error = $6,
                    completed_at = now()
                WHERE turn_id = $1 AND role = 'assistant'
                RETURNING session_id
                """,
                turn_id,
                content,
                activity,
                status,
                tool_used,
                error,
            )
            if row is None:
                return
            session_id = row["session_id"]
            await conn.execute(
                """
                UPDATE chat_sessions
                SET last_message_at = now(), updated_at = now()
                WHERE id = $1
                """,
                session_id,
            )
            event_type = (
                events.CHAT_TURN_FAILED if status == "failed" else events.CHAT_TURN_COMPLETED
            )
            payload: dict[str, Any] = {
                "turn_id": str(turn_id),
                "status": status,
                "tool_used": tool_used,
                "assistant_chars": len(content),
            }
            if error is not None:
                payload["error"] = error
            await audit.write_audit_event(
                conn,
                event_type,
                actor=f"chat:{session_id}",
                payload=payload,
            )


async def _load_history_for_llm(
    pool: asyncpg.Pool,
    session_id: UUID,
    limit: int = 30,
) -> list[dict[str, str]]:
    """Return the last `limit` complete messages as {role, content} dicts,
    oldest first, for feeding back to the LLM. Skips streaming/failed rows."""
    rows = await pool.fetch(
        """
        SELECT role, content FROM chat_messages
        WHERE session_id = $1
          AND status = 'complete'
          AND content <> ''
        ORDER BY id DESC
        LIMIT $2
        """,
        session_id,
        limit,
    )
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


# ── Turn runtime ────────────────────────────────────────────────────────────

# Module-level registry — keeps a reference to running task objects so they
# aren't garbage-collected (per asyncio.create_task docs).
_ACTIVE_TURNS: dict[UUID, "TurnRuntime"] = {}


class TurnRuntime:
    """Owns one chat turn end to end.

    Created and registered by `start_turn`. Holds:
      - a single optional subscriber queue (the originating request's SSE)
      - the running activity tree + cursor state
      - the running content buffer (final assistant text)
      - the asyncio task driving the OpenAI loop

    On completion, writes the final state to chat_messages and removes itself
    from `_ACTIVE_TURNS` via its `done_callback`.
    """

    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
        session_id: UUID,
        turn_id: UUID,
        history: list[dict[str, str]],
    ) -> None:
        self.pool = pool
        self.session_id = session_id
        self.turn_id = turn_id
        self._history = history

        self._activity: list[dict[str, Any]] = []
        self._activity_state = ActivityState()
        self._content_parts: list[str] = []
        self._tool_used: str | None = None
        self._subscriber: asyncio.Queue[dict[str, Any] | None] | None = None
        self._done = False

        self.task: asyncio.Task | None = None

    def subscribe(self) -> asyncio.Queue[dict[str, Any] | None] | None:
        """Attach a single live subscriber. Returns the queue (None sentinel
        signals end of stream). If the turn has already completed, returns None
        — caller should fetch the persisted chat_messages row instead.
        """
        if self._done:
            return None
        if self._subscriber is not None:
            # Drop the prior subscriber — at most one at a time. The old client
            # can fall back to polling chat_messages.
            try:
                self._subscriber.put_nowait(None)
            except asyncio.QueueFull:
                pass
        self._subscriber = asyncio.Queue()
        return self._subscriber

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any] | None]) -> None:
        if self._subscriber is queue:
            self._subscriber = None

    def _emit_to_subscriber(self, event: dict[str, Any]) -> None:
        sub = self._subscriber
        if sub is None:
            return
        try:
            sub.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("chat turn subscriber queue full; dropping event")

    def _process_event(self, event: dict[str, Any]) -> None:
        """Update in-memory state for one event, then push to the subscriber."""
        etype = event.get("type")
        if etype == "delta":
            text = event.get("text")
            if isinstance(text, str):
                self._content_parts.append(text)
        elif etype == "tool_call_started":
            name = event.get("name")
            if isinstance(name, str):
                self._tool_used = name
        elif etype == "done":
            answer = event.get("answer")
            if isinstance(answer, str) and answer and not self._content_parts:
                # Belt and suspenders: if no deltas accumulated, use final answer.
                self._content_parts.append(answer)

        apply_event(self._activity, self._activity_state, event)
        self._emit_to_subscriber(event)

    async def run(self) -> None:
        """Drive the OpenAI tool-call loop and persist the final state.

        Cancellation safety: this task is created via asyncio.create_task at
        the top level, so it is NOT part of the request's cancellation scope.
        The request handler can return / disconnect / be cancelled without
        cancelling us.
        """
        terminal_status = "complete"
        error_message: str | None = None
        try:
            async for event in _stream_llm_turn(self._history):
                self._process_event(event)
                if event.get("type") == "error":
                    terminal_status = "failed"
                    error_message = (
                        event.get("message") if isinstance(event.get("message"), str) else "error"
                    )
        except asyncio.CancelledError:
            terminal_status = "failed"
            error_message = "task cancelled"
            raise
        except Exception as exc:
            logger.exception("Chat turn %s failed", self.turn_id)
            terminal_status = "failed"
            error_message = f"{type(exc).__name__}: {exc}"
            self._emit_to_subscriber({"type": "error", "message": str(exc)})
        finally:
            self._done = True
            try:
                await _finalize_assistant_message(
                    self.pool,
                    turn_id=self.turn_id,
                    content="".join(self._content_parts),
                    activity=self._activity,
                    status=terminal_status,
                    tool_used=self._tool_used,
                    error=error_message,
                )
            except Exception:
                logger.exception("Failed to finalize chat message for turn %s", self.turn_id)
            sub = self._subscriber
            if sub is not None:
                try:
                    sub.put_nowait(None)
                except asyncio.QueueFull:
                    pass


# ── Public API ──────────────────────────────────────────────────────────────


async def start_turn(
    pool: asyncpg.Pool,
    session_id: UUID,
    content: str,
) -> TurnRuntime:
    """Start one chat turn end to end:
      1. Append the user message + a streaming placeholder in one transaction.
      2. Load message history for the LLM.
      3. Construct a `TurnRuntime` and spawn its background task.
      4. Return the runtime so the caller can `.subscribe()` immediately.
    """
    turn_id, _placeholder_id = await _append_user_message_and_prepare_turn(
        pool, session_id, content
    )
    history = await _load_history_for_llm(pool, session_id, limit=30)

    runtime = TurnRuntime(
        pool=pool,
        session_id=session_id,
        turn_id=turn_id,
        history=history,
    )
    _ACTIVE_TURNS[turn_id] = runtime

    task = asyncio.create_task(runtime.run(), name=f"chat-turn-{turn_id}")
    runtime.task = task

    def _cleanup(_t: asyncio.Task) -> None:
        _ACTIVE_TURNS.pop(turn_id, None)

    task.add_done_callback(_cleanup)
    return runtime


def get_active(turn_id: UUID) -> TurnRuntime | None:
    return _ACTIVE_TURNS.get(turn_id)
